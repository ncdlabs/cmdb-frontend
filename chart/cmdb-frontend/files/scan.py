"""One-shot LAN discovery of hosts not already in inventory (IPv4 + IPv6)."""

from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import os
import re
import socket
import ssl
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from cmdb_api.addresses import AddressExtractor

# Persisted under CMDB_ROOT (PVC) — scan-to-scan baseline, not inventory YAML.
LAST_NETWORK_SCAN_REL = Path(".cmdb") / "last-network-scan.json"
_COMPARE_FIELDS = ("hostname", "ports", "mac", "ssh_open", "ping", "family")
LAN_RESCAN_NOTE = "Added from LAN rescan. Confirm identity before promoting status."

# Light fingerprint ports — same spirit as prior unidentified LAN notes.
PROBE_PORTS = (22, 80, 443, 445, 8080, 8443)
PING_TIMEOUT_S = 1
TCP_TIMEOUT_S = 0.35
MAX_WORKERS = 64
# Never brute-force larger IPv6 prefixes (a /64 is ~1.8e19).
IPV6_SWEEP_MAX_HOSTS = 256
# Cap IPv4 sweeps too — a mis-set /8 or /0 must not hang the API.
IPV4_SWEEP_MAX_HOSTS = 1024
NEIGH_OK_STATES = {"REACHABLE", "STALE", "DELAY", "PROBE", "PERMANENT", "NOARP"}

# Cluster / overlay ranges — never treat as the site LAN when deriving /24s.
_CLUSTER_V4_PREFIXES = (
    ipaddress.ip_network("10.42.0.0/16"),  # k3s pods
    ipaddress.ip_network("10.43.0.0/16"),  # k3s services
    ipaddress.ip_network("10.244.0.0/16"),  # flannel
    ipaddress.ip_network("10.96.0.0/12"),  # common kube service CIDR
)

# Re-export AddressExtractor helpers so existing imports keep working.
normalize_ip = AddressExtractor.normalize_ip
ip_from_cidr_or_addr = AddressExtractor.from_cidr_or_addr
ipv4_from_cidr_or_addr = AddressExtractor.ipv4_from_cidr_or_addr
ipv6_from_cidr_or_addr = AddressExtractor.ipv6_from_cidr_or_addr
_is_ipv4 = AddressExtractor.is_ipv4
_is_ipv6 = AddressExtractor.is_ipv6


def _utc_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _iface_addrs(iface: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("ipv4", "ipv6", "addr", "address"):
        ip = ip_from_cidr_or_addr(iface.get(key))
        if ip:
            out.append(ip)
    for key in ("addresses", "addrs"):
        raw = iface.get(key)
        if isinstance(raw, list):
            for entry in raw:
                ip = ip_from_cidr_or_addr(entry)
                if ip:
                    out.append(ip)
        else:
            ip = ip_from_cidr_or_addr(raw)
            if ip:
                out.append(ip)
    return out


def collect_known_ips(items: Iterable[dict[str, Any]]) -> set[str]:
    """Primary inventory IPs (v4/v6 addresses, ssh host, interface addrs)."""
    known: set[str] = set()
    for item in items:
        addresses = item.get("addresses")
        if isinstance(addresses, dict):
            for key in ("ipv4", "ipv6", "tailscale"):
                ip = ip_from_cidr_or_addr(addresses.get(key))
                if ip:
                    known.add(ip)
        host = AddressExtractor.ssh_host(item.get("ssh"))
        if host:
            ip = ip_from_cidr_or_addr(host)
            if ip:
                known.add(ip)
        network = item.get("network")
        if isinstance(network, dict):
            interfaces = network.get("interfaces")
            if isinstance(interfaces, list):
                for iface in interfaces:
                    if isinstance(iface, dict):
                        known.update(_iface_addrs(iface))
    return known


def collect_known_ipv4s(items: Iterable[dict[str, Any]]) -> set[str]:
    return {ip for ip in collect_known_ips(items) if _is_ipv4(ip)}


def collect_unidentified_ips(items: Iterable[dict[str, Any]]) -> set[str]:
    """IPs already noted under observed_ips holding tanks."""
    found: set[str] = set()
    for item in items:
        rows = item.get("observed_ips")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                ip = ip_from_cidr_or_addr(row.get("ip") or row.get("ipv4") or row.get("ipv6"))
            elif isinstance(row, str):
                ip = ip_from_cidr_or_addr(row)
            else:
                ip = None
            if ip:
                found.add(ip)
    return found


def collect_unidentified_ipv4s(items: Iterable[dict[str, Any]]) -> set[str]:
    return {ip for ip in collect_unidentified_ips(items) if _is_ipv4(ip)}


def collect_inventory_hostnames(items: Iterable[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for item in items:
        addresses = item.get("addresses")
        if isinstance(addresses, dict):
            hostname = addresses.get("hostname")
            if isinstance(hostname, str) and hostname.strip():
                names.add(hostname.strip().rstrip("."))
            dns = addresses.get("dns")
            if isinstance(dns, list):
                for entry in dns:
                    if isinstance(entry, str) and entry.strip():
                        names.add(entry.strip().rstrip("."))
            elif isinstance(dns, str) and dns.strip():
                names.add(dns.strip().rstrip("."))
        name = item.get("name")
        if isinstance(name, str) and name.strip() and "." in name:
            names.add(name.strip().rstrip("."))
    return names


def _is_cluster_overlay_v4(addr: ipaddress.IPv4Address) -> bool:
    return any(addr in prefix for prefix in _CLUSTER_V4_PREFIXES)


def private_lan_slash24(ip: str) -> str | None:
    """Return ``a.b.c.0/24`` for a private site IP; skip loopback/link-local/cluster overlays."""
    try:
        addr = ipaddress.IPv4Address(ip.strip())
    except ValueError:
        return None
    if not addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast:
        return None
    if _is_cluster_overlay_v4(addr):
        return None
    return str(ipaddress.ip_network(f"{addr}/24", strict=False))


def _parse_cidr_list(raw: str) -> list[str]:
    out: list[str] = []
    for part in raw.replace(";", ",").split(","):
        cidr = part.strip()
        if not cidr:
            continue
        try:
            ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        out.append(cidr)
    return out


def _local_iface_private_v4() -> list[str]:
    """Private non-overlay IPv4s from ``ip -4 -o addr`` (useful with hostNetwork)."""
    try:
        proc = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", "scope", "global"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    found: list[str] = []
    for line in (proc.stdout or "").splitlines():
        # ... inet 192.168.1.10/24 ...
        match = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+)(?:/\d+)?", line)
        if not match:
            continue
        slash24 = private_lan_slash24(match.group(1))
        if slash24:
            found.append(slash24)
    return found


def collect_runtime_lan_cidrs(extra_cidrs: list[str] | None = None) -> list[str]:
    """LAN prefixes from deploy env + optional PVC settings + node host IP + ifaces."""
    found: set[str] = set()
    for cidr in _parse_cidr_list(os.environ.get("CMDB_LAN_CIDR", "")):
        found.add(str(ipaddress.ip_network(cidr, strict=False)))
    for cidr in extra_cidrs or []:
        try:
            found.add(str(ipaddress.ip_network(str(cidr).strip(), strict=False)))
        except ValueError:
            continue
    node_ip = (os.environ.get("CMDB_NODE_IP") or "").strip()
    slash24 = private_lan_slash24(node_ip)
    if slash24:
        found.add(slash24)
    found.update(_local_iface_private_v4())
    return sorted(found)


def derive_subnets(
    items: Iterable[dict[str, Any]],
    *,
    extra_lan_cidrs: list[str] | None = None,
) -> list[str]:
    """IPv4 + IPv6 prefixes from runtime env, PVC settings, inventory, and server addresses.

    Sources are **unioned** so a leftover sample ``lan_cidr`` (e.g. ``10.0.0.0/24``) cannot
    hide the real site LAN when ``CMDB_NODE_IP`` / ``CMDB_LAN_CIDR`` / settings / host
    interfaces provide a private non-overlay prefix.
    """
    v4: set[str] = set()
    v6: set[str] = set()

    def _add_network(raw: str) -> None:
        try:
            network_obj = ipaddress.ip_network(raw.strip(), strict=False)
        except ValueError:
            return
        if isinstance(network_obj, ipaddress.IPv4Network):
            v4.add(str(network_obj))
        elif isinstance(network_obj, ipaddress.IPv6Network):
            # Never retain link-local scan prefixes.
            if network_obj.network_address.is_link_local:
                return
            v6.add(str(network_obj))

    for cidr in collect_runtime_lan_cidrs(extra_lan_cidrs):
        _add_network(cidr)

    for item in items:
        if str(item.get("kind") or "") != "environment":
            continue
        network = item.get("network")
        if not isinstance(network, dict):
            continue
        for key in ("lan_cidr", "lan_cidr_v6", "ula_cidr", "ipv6_cidr"):
            val = network.get(key)
            if isinstance(val, str) and val.strip():
                _add_network(val)
            elif isinstance(val, list):
                for entry in val:
                    if isinstance(entry, str) and entry.strip():
                        _add_network(entry)

    for item in items:
        if str(item.get("kind") or "") != "server":
            continue
        addresses = item.get("addresses")
        if isinstance(addresses, dict):
            ip4 = ipv4_from_cidr_or_addr(addresses.get("ipv4"))
            if ip4:
                slash24 = private_lan_slash24(ip4)
                if slash24:
                    v4.add(slash24)
            ip6 = ipv6_from_cidr_or_addr(addresses.get("ipv6"))
            if ip6:
                try:
                    addr = ipaddress.IPv6Address(ip6)
                    if not addr.is_link_local:
                        v6.add(str(ipaddress.ip_network(f"{ip6}/64", strict=False)))
                except ValueError:
                    pass
        network = item.get("network")
        if isinstance(network, dict):
            interfaces = network.get("interfaces")
            if isinstance(interfaces, list):
                for iface in interfaces:
                    if not isinstance(iface, dict):
                        continue
                    for candidate in _iface_addrs(iface):
                        if _is_ipv4(candidate):
                            slash24 = private_lan_slash24(candidate)
                            if slash24:
                                v4.add(slash24)
                            continue
                        if not _is_ipv6(candidate):
                            continue
                        try:
                            addr = ipaddress.IPv6Address(candidate)
                            if addr.is_link_local:
                                continue
                            v6.add(str(ipaddress.ip_network(f"{candidate}/64", strict=False)))
                        except ValueError:
                            continue

    return sorted(v4 | v6)


def _ping_up(ip: str) -> bool:
    try:
        if _is_ipv6(ip):
            cmd = ["ping", "-6", "-c", "1", "-W", str(PING_TIMEOUT_S), ip]
        else:
            cmd = ["ping", "-c", "1", "-W", str(PING_TIMEOUT_S), ip]
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=PING_TIMEOUT_S + 1.5,
            check=False,
        )
        return completed.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _open_ports(ip: str, ports: tuple[int, ...] = PROBE_PORTS) -> list[int]:
    family = socket.AF_INET6 if _is_ipv6(ip) else socket.AF_INET
    open_ports: list[int] = []
    for port in ports:
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(TCP_TIMEOUT_S)
        try:
            if family == socket.AF_INET6:
                if sock.connect_ex((ip, port, 0, 0)) == 0:
                    open_ports.append(port)
            elif sock.connect_ex((ip, port)) == 0:
                open_ports.append(port)
        except OSError:
            continue
        finally:
            sock.close()
    return open_ports


def _reverse_dns(ip: str) -> str | None:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    host: str | None = None
    try:
        if isinstance(addr, ipaddress.IPv6Address):
            host, _service = socket.getnameinfo((str(addr), 0, 0, 0), 0)
        else:
            host, _service = socket.getnameinfo((str(addr), 0), 0)
    except OSError:
        host = None
    if not host or host == ip or normalize_ip(host) == str(addr):
        try:
            host, _aliases, _addrs = socket.gethostbyaddr(ip)
        except (socket.herror, socket.gaierror, OSError):
            return None
    if not host or host == ip or normalize_ip(host) == str(addr):
        return None
    return host.rstrip(".")


def _a_lookup(hostname: str) -> list[str]:
    out: list[str] = []
    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_STREAM)
    except (socket.gaierror, OSError):
        return out
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        ip = normalize_ip(str(sockaddr[0]))
        if ip and _is_ipv4(ip):
            out.append(ip)
    return sorted(set(out))


def _aaaa_lookup(hostname: str) -> list[str]:
    out: list[str] = []
    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_INET6, socket.SOCK_STREAM)
    except (socket.gaierror, OSError):
        return out
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        ip = normalize_ip(str(sockaddr[0]))
        if not ip or not _is_ipv6(ip):
            continue
        addr = ipaddress.IPv6Address(ip)
        if addr.is_link_local or addr.ipv4_mapped is not None:
            continue
        out.append(ip)
    return sorted(set(out))


def _hostname_candidates(name: str) -> list[str]:
    base = name.strip().rstrip(".")
    if not base:
        return []
    out = [base]
    if "." not in base:
        out.extend([f"{base}.local", f"{base}.example.com", f"{base}.lan"])
    # Preserve order, unique
    seen: set[str] = set()
    ordered: list[str] = []
    for cand in out:
        key = cand.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(cand)
    return ordered


def build_hostname_hints(hostnames: Iterable[str]) -> dict[str, str]:
    """Map IP → inventory/DNS hostname via forward A/AAAA lookups."""
    hints: dict[str, str] = {}
    for name in hostnames:
        for cand in _hostname_candidates(name):
            short = cand.split(".")[0]
            for ip in _a_lookup(cand) + _aaaa_lookup(cand):
                prev = hints.get(ip)
                # Prefer the short unqualified label when tied.
                if prev is None or ( "." in prev and "." not in short) or len(short) < len(prev.split(".")[0]):
                    hints[ip] = cand
    return hints


def _tls_hostname(ip: str, port: int) -> str | None:
    """Best-effort peer certificate CN/SAN (common on printers, NAS, appliances)."""
    try:
        # getpeercert() is empty when verify is disabled; fetch PEM and parse with openssl.
        pem = ssl.get_server_certificate((ip, port), timeout=TCP_TIMEOUT_S + 1.5)
    except OSError:
        # IPv6 literals need brackets for OpenSSL helpers on some builds — fall back to socket wrap.
        pem = None
        try:
            family = socket.AF_INET6 if _is_ipv6(ip) else socket.AF_INET
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            raw = socket.socket(family, socket.SOCK_STREAM)
            raw.settimeout(TCP_TIMEOUT_S + 1.5)
            if family == socket.AF_INET6:
                raw.connect((ip, port, 0, 0))
            else:
                raw.connect((ip, port))
            with ctx.wrap_socket(raw, server_hostname=ip) as ssock:
                der = ssock.getpeercert(binary_form=True)
            if not der:
                return None
            completed = subprocess.run(
                ["openssl", "x509", "-inform", "DER", "-noout", "-subject", "-ext", "subjectAltName"],
                input=der,
                capture_output=True,
                timeout=3,
                check=False,
            )
            text = (completed.stdout or b"").decode("utf-8", errors="ignore")
            return _hostname_from_openssl_text(text)
        except (OSError, subprocess.TimeoutExpired):
            return None

    if not pem:
        return None
    try:
        completed = subprocess.run(
            ["openssl", "x509", "-noout", "-subject", "-ext", "subjectAltName"],
            input=pem.encode(),
            capture_output=True,
            timeout=3,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    text = (completed.stdout or b"").decode("utf-8", errors="ignore")
    return _hostname_from_openssl_text(text)


def _hostname_from_openssl_text(text: str) -> str | None:
    # subject=CN = foo.example
    for m in re.finditer(r"DNS:([A-Za-z0-9_.*-]+)", text):
        name = m.group(1).strip().rstrip(".").lstrip("*.")
        if name and not _is_ipv4(name) and not _is_ipv6(name):
            return name
    m = re.search(r"\bCN\s*=\s*([^/\n]+)", text, re.I)
    if m:
        name = m.group(1).strip().rstrip(".").lstrip("*.")
        # Drop trailing RDNs if any slipped through (e.g. "foo, O = Bar")
        name = name.split(",")[0].strip()
        if name and not _is_ipv4(name) and not _is_ipv6(name) and len(name) <= 80:
            return name
    return None


def _http_hostname(ip: str, port: int) -> str | None:
    """Best-effort Host / Location / title from a tiny HTTP probe."""
    family = socket.AF_INET6 if _is_ipv6(ip) else socket.AF_INET
    host_header = f"[{ip}]" if _is_ipv6(ip) else ip
    req = (
        f"GET / HTTP/1.0\r\nHost: {host_header}\r\nUser-Agent: cmdb-scan\r\nConnection: close\r\n\r\n"
    ).encode()
    try:
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(TCP_TIMEOUT_S)
        if family == socket.AF_INET6:
            sock.connect((ip, port, 0, 0))
        else:
            sock.connect((ip, port))
        sock.sendall(req)
        chunks: list[bytes] = []
        while sum(len(c) for c in chunks) < 4096:
            data = sock.recv(1024)
            if not data:
                break
            chunks.append(data)
        sock.close()
    except OSError:
        return None
    text = b"".join(chunks).decode("utf-8", errors="ignore")
    # Location: http://hostname/...
    for line in text.splitlines():
        if line.lower().startswith("location:"):
            loc = line.split(":", 1)[1].strip()
            m = re.search(r"https?://([^/:]+)", loc, re.I)
            if m:
                name = m.group(1).strip().rstrip(".")
                if name and name.lower() not in {host_header.lower(), ip.lower()} and not _is_ipv4(name):
                    return name
    m = re.search(r"<title[^>]*>([^<]+)</title>", text, re.I)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()
        # Titles are often product names; only use if they look hostname-like.
        if title and " " not in title and 2 <= len(title) <= 64 and not title.lower().startswith("http"):
            return title
    return None


def _service_hostname(ip: str, ports: list[int]) -> str | None:
    for port in (443, 8443, 80, 8080):
        if port not in ports:
            continue
        if port in (443, 8443):
            name = _tls_hostname(ip, port)
        else:
            name = _http_hostname(ip, port)
        if name:
            return name
    return None


def _probe_host(ip: str, *, source: str, hostname_hint: str | None = None) -> dict[str, Any] | None:
    ping_ok = _ping_up(ip)
    ports = _open_ports(ip)
    # NDP/AAAA sightings count even without open ports / ping.
    if not ping_ok and not ports and source not in {"ndp", "aaaa"}:
        return None
    hostname = _reverse_dns(ip) or hostname_hint
    if not hostname and ports:
        hostname = _service_hostname(ip, ports)
    family = "ipv6" if _is_ipv6(ip) else "ipv4"
    return {
        "ip": ip,
        "family": family,
        "hostname": hostname,
        "ping": ping_ok,
        "ports": ports,
        "ssh_open": 22 in ports,
        "source": source,
    }


def read_neighbors(*, ipv6: bool) -> list[dict[str, str]]:
    """Parse `ip -4|-6 neigh` (meaningful under hostNetwork)."""
    flag = "-6" if ipv6 else "-4"
    try:
        completed = subprocess.run(
            ["ip", flag, "neigh", "show"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if completed.returncode != 0:
        return []

    neighbors: list[dict[str, str]] = []
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) < 1:
            continue
        ip = ipv6_from_cidr_or_addr(parts[0]) if ipv6 else ipv4_from_cidr_or_addr(parts[0])
        if not ip:
            continue
        state = ""
        lladdr = ""
        for idx, token in enumerate(parts):
            if token == "lladdr" and idx + 1 < len(parts):
                lladdr = parts[idx + 1]
            if token in NEIGH_OK_STATES:
                state = token
        if not state:
            # Lines like "fe80::1 dev eth0 FAILED" — skip failed/incomplete.
            upper = {p.upper() for p in parts}
            if upper & {"FAILED", "INCOMPLETE"}:
                continue
            state = "UNKNOWN"
        neighbors.append({"ip": ip, "lladdr": lladdr, "state": state})
    return neighbors


def read_ipv6_neighbors() -> list[dict[str, str]]:
    """Parse `ip -6 neigh` (meaningful under hostNetwork)."""
    return read_neighbors(ipv6=True)


def read_ipv4_neighbors() -> list[dict[str, str]]:
    """Parse `ip -4 neigh` (ARP table; meaningful under hostNetwork)."""
    return read_neighbors(ipv6=False)


def _ip_in_prefixes(ip: str, prefixes: list[Any]) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in prefixes)


def _sort_discovered(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(row: dict[str, Any]) -> tuple[Any, ...]:
        ip = row.get("ip") or ""
        try:
            addr = ipaddress.ip_address(ip)
            return (addr.version, int(addr))
        except ValueError:
            return (9, 0, ip)

    return sorted(rows, key=key)


def observation_record(row: dict[str, Any]) -> dict[str, Any]:
    """Stable fingerprint of a scan sighting for snapshot / diff."""
    ports = row.get("ports") or []
    if not isinstance(ports, list):
        ports = []
    return {
        "ip": str(row.get("ip") or ""),
        "family": row.get("family"),
        "hostname": row.get("hostname") or None,
        "ports": sorted(int(p) for p in ports if isinstance(p, int) or str(p).isdigit()),
        "ssh_open": bool(row.get("ssh_open")),
        "ping": bool(row.get("ping")),
        "mac": row.get("mac") or None,
        "source": row.get("source"),
        "in_inventory": bool(row.get("in_inventory")),
    }


def _comparable(record: dict[str, Any]) -> dict[str, Any]:
    return {field: record.get(field) for field in _COMPARE_FIELDS}


def diff_observations(
    previous: dict[str, Any] | None,
    current_hosts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare current observations to the last persisted scan snapshot."""
    current_map = {
        rec["ip"]: rec
        for rec in (observation_record(h) for h in current_hosts)
        if rec["ip"]
    }
    if not previous or not isinstance(previous.get("hosts"), list):
        return {
            "previous_scanned_at": None,
            "baseline": False,
            "added": [],
            "removed": [],
            "modified": [],
            "counts": {"added": 0, "removed": 0, "modified": 0},
            "note": "First scan — no previous baseline to compare.",
        }

    prev_map: dict[str, dict[str, Any]] = {}
    for raw in previous["hosts"]:
        if not isinstance(raw, dict):
            continue
        rec = observation_record(raw)
        if rec["ip"]:
            prev_map[rec["ip"]] = rec

    added = _sort_discovered([current_map[ip] for ip in current_map.keys() - prev_map.keys()])
    removed = _sort_discovered([prev_map[ip] for ip in prev_map.keys() - current_map.keys()])
    modified: list[dict[str, Any]] = []
    for ip in sorted(current_map.keys() & prev_map.keys(), key=lambda x: x):
        before = _comparable(prev_map[ip])
        after = _comparable(current_map[ip])
        changes = [field for field in _COMPARE_FIELDS if before.get(field) != after.get(field)]
        if changes:
            modified.append(
                {
                    "ip": ip,
                    "changes": changes,
                    "before": before,
                    "after": after,
                    "hostname": current_map[ip].get("hostname"),
                    "family": current_map[ip].get("family"),
                }
            )

    return {
        "previous_scanned_at": previous.get("scanned_at"),
        "baseline": True,
        "added": added,
        "removed": removed,
        "modified": modified,
        "counts": {
            "added": len(added),
            "removed": len(removed),
            "modified": len(modified),
        },
    }


def load_last_network_scan(root: Path) -> dict[str, Any] | None:
    path = root / LAST_NETWORK_SCAN_REL
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_last_network_scan(
    root: Path,
    *,
    scanned_at: str,
    subnets: list[str],
    hosts: list[dict[str, Any]],
) -> str | None:
    """Persist snapshot under CMDB_ROOT. Returns relative path or None if not writable."""
    path = root / LAST_NETWORK_SCAN_REL
    payload = {
        "scanned_at": scanned_at,
        "subnets": list(subnets),
        "hosts": [observation_record(h) for h in hosts],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
        tmp.replace(path)
        return str(LAST_NETWORK_SCAN_REL)
    except OSError:
        return None


def _keep_sighting(result: dict[str, Any]) -> bool:
    return bool(
        result.get("source") in {"ndp", "arp", "aaaa"}
        or result.get("ports")
        or result.get("ping")
    )


_MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$", re.I)
_SOURCE_PRIORITY = {
    "ndp": 4,
    "arp": 4,
    "aaaa": 3,
    "ipv6-sweep": 2,
    "ipv4-sweep": 1,
}


def normalize_mac(raw: Any) -> str | None:
    """Normalize MAC to lowercase colon form, or None if invalid."""
    if not isinstance(raw, str):
        return None
    s = raw.strip().lower().replace("-", ":")
    if "." in s and ":" not in s:
        hexonly = re.sub(r"[^0-9a-f]", "", s)
        if len(hexonly) == 12:
            s = ":".join(hexonly[i : i + 2] for i in range(0, 12, 2))
    if not _MAC_RE.match(s):
        return None
    return s


def hostname_merge_key(hostname: Any) -> str | None:
    if not isinstance(hostname, str):
        return None
    key = hostname.strip().lower().rstrip(".")
    return key or None


def merge_scan_add_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Coalesce complementary IPv4+IPv6 selections that share a hostname into one
    device so Add writes a single dual-stack server CI.
    """
    if len(entries) < 2:
        return list(entries)

    def _family_of(entry: dict[str, Any]) -> str | None:
        ipv4 = ipv4_from_cidr_or_addr(entry.get("ipv4") or entry.get("ip"))
        ipv6 = ipv6_from_cidr_or_addr(entry.get("ipv6"))
        if not ipv6:
            raw_ip = str(entry.get("ip") or "")
            if ":" in raw_ip:
                ipv6 = ipv6_from_cidr_or_addr(raw_ip)
        if ipv4 and not ipv6:
            return "ipv4"
        if ipv6 and not ipv4:
            return "ipv6"
        if ipv4 and ipv6:
            return "both"
        return None

    def _ports_of(entry: dict[str, Any]) -> list[int]:
        raw = entry.get("ports") or []
        out: list[int] = []
        for p in raw:
            if isinstance(p, (int, float, str)) and str(p).isdigit():
                n = int(p)
                if 1 <= n <= 65535:
                    out.append(n)
        return out

    def _merge_pair(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
        a_v4 = ipv4_from_cidr_or_addr(a.get("ipv4") or (a.get("ip") if ":" not in str(a.get("ip") or "") else None))
        a_v6 = ipv6_from_cidr_or_addr(a.get("ipv6") or (a.get("ip") if ":" in str(a.get("ip") or "") else None))
        b_v4 = ipv4_from_cidr_or_addr(b.get("ipv4") or (b.get("ip") if ":" not in str(b.get("ip") or "") else None))
        b_v6 = ipv6_from_cidr_or_addr(b.get("ipv6") or (b.get("ip") if ":" in str(b.get("ip") or "") else None))
        ipv4 = a_v4 or b_v4
        ipv6 = a_v6 or b_v6
        primary = ipv4 or ipv6 or str(a.get("ip") or b.get("ip") or "")
        ports = sorted(set(_ports_of(a) + _ports_of(b)))
        mac = normalize_mac(a.get("mac")) or normalize_mac(b.get("mac"))
        src_a = str(a.get("source") or "")
        src_b = str(b.get("source") or "")
        source = (
            src_a
            if _SOURCE_PRIORITY.get(src_a, 0) >= _SOURCE_PRIORITY.get(src_b, 0)
            else src_b
        ) or None
        # Prefer draft fields from the IPv4 row when both provide id/name.
        prefer, other = (a, b) if a_v4 else (b, a) if b_v4 else (a, b)
        item_id = str(prefer.get("id") or other.get("id") or "").strip() or None
        name = str(prefer.get("name") or other.get("name") or "").strip() or None
        hostname_raw = str(prefer.get("hostname") or other.get("hostname") or "").strip()
        hostname = hostname_raw or None
        merged: dict[str, Any] = {
            **prefer,
            "ip": primary,
            "ipv4": ipv4,
            "ipv6": ipv6,
            "ports": ports,
            "mac": mac,
            "source": source,
            "hostname": hostname,
        }
        if item_id:
            merged["id"] = item_id
        if name:
            merged["name"] = name
        return merged

    pending = list(entries)
    out: list[dict[str, Any]] = []
    used: set[int] = set()
    for i, entry in enumerate(pending):
        if i in used:
            continue
        key = hostname_merge_key(entry.get("hostname"))
        fam = _family_of(entry)
        if not key or fam not in {"ipv4", "ipv6"}:
            out.append(entry)
            used.add(i)
            continue
        partner_idx: int | None = None
        want = "ipv6" if fam == "ipv4" else "ipv4"
        for j in range(i + 1, len(pending)):
            if j in used:
                continue
            other = pending[j]
            if hostname_merge_key(other.get("hostname")) != key:
                continue
            if _family_of(other) == want:
                partner_idx = j
                break
        if partner_idx is None:
            out.append(entry)
            used.add(i)
            continue
        out.append(_merge_pair(entry, pending[partner_idx]))
        used.add(i)
        used.add(partner_idx)
    return out


def scan_subnets(
    subnets: list[str],
    known_ips: set[str],
    *,
    hostnames: Iterable[str] | None = None,
) -> dict[str, Any]:
    """
    Discover LAN hosts:
    - IPv4: sweep inventory-derived prefixes + ARP neighbor MACs (`ip -4 neigh`)
    - IPv6: NDP neighbor table + AAAA for hostnames (never sweep a /64)
    - Small IPv6 prefixes (≤ /120 by default) may be swept
    """
    scanned_at = datetime.now(timezone.utc).isoformat()
    known_norm = {normalize_ip(ip) or ip for ip in known_ips}
    targets: list[tuple[str, str]] = []  # ip, source
    notes: list[str] = []

    v4_nets: list[ipaddress.IPv4Network] = []
    v6_nets: list[ipaddress.IPv6Network] = []
    for cidr in subnets:
        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        if isinstance(network, ipaddress.IPv4Network):
            v4_nets.append(network)
        elif isinstance(network, ipaddress.IPv6Network):
            v6_nets.append(network)

    for network in v4_nets:
        host_count = network.num_addresses if network.num_addresses <= 2 else network.num_addresses - 2
        if host_count > IPV4_SWEEP_MAX_HOSTS:
            notes.append(
                f"Skipping IPv4 sweep of {network} ({host_count} hosts); "
                f"max is {IPV4_SWEEP_MAX_HOSTS} — narrow env.network.lan_cidr"
            )
            continue
        hosts = list(network.hosts()) if network.num_addresses > 2 else list(network)
        for host in hosts:
            targets.append((str(host), "ipv4-sweep"))

    for network in v6_nets:
        # hosts() excludes network/broadcast-style endpoints where applicable.
        host_count = network.num_addresses if network.num_addresses <= 2 else network.num_addresses - 2
        if host_count > IPV6_SWEEP_MAX_HOSTS:
            notes.append(
                f"Skipping IPv6 sweep of {network} ({host_count} hosts); "
                "using NDP + AAAA only for large prefixes"
            )
            continue
        hosts = list(network.hosts()) if network.num_addresses > 2 else list(network)
        for host in hosts:
            targets.append((str(host), "ipv6-sweep"))

    neigh_macs: dict[str, str] = {}
    ndp_prefix_hint: set[str] = set()
    for neigh in read_ipv6_neighbors():
        ip = neigh["ip"]
        addr = ipaddress.IPv6Address(ip)
        # Link-local is noisy and not useful as inventory identity — skip.
        if addr.is_link_local or addr.is_multicast or addr.is_unspecified:
            continue
        try:
            ndp_prefix_hint.add(str(ipaddress.ip_network(f"{ip}/64", strict=False)))
        except ValueError:
            pass
        if v6_nets and not _ip_in_prefixes(ip, v6_nets):
            continue
        mac = normalize_mac(neigh.get("lladdr"))
        if mac:
            neigh_macs[ip] = mac
        targets.append((ip, "ndp"))

    for neigh in read_ipv4_neighbors():
        ip = neigh["ip"]
        try:
            addr = ipaddress.IPv4Address(ip)
        except ValueError:
            continue
        if addr.is_link_local or addr.is_multicast or addr.is_unspecified or addr.is_loopback:
            continue
        if v4_nets and not _ip_in_prefixes(ip, v4_nets):
            continue
        mac = normalize_mac(neigh.get("lladdr"))
        if mac:
            neigh_macs[ip] = mac
        targets.append((ip, "arp"))

    # AAAA only kept inside inventory prefixes, else prefixes observed via NDP.
    aaaa_nets: list[Any] = list(v6_nets)
    if not aaaa_nets:
        for cidr in sorted(ndp_prefix_hint):
            try:
                net = ipaddress.ip_network(cidr, strict=False)
            except ValueError:
                continue
            if isinstance(net, ipaddress.IPv6Network):
                aaaa_nets.append(net)

    hostname_set = {h.strip().rstrip(".") for h in (hostnames or []) if h and h.strip()}
    hostname_hints = build_hostname_hints(hostname_set)
    for host in hostname_set:
        for cand in _hostname_candidates(host):
            for ip in _aaaa_lookup(cand):
                # Without a LAN IPv6 prefix hint, skip AAAA (avoids public CDN answers).
                if not aaaa_nets or not _ip_in_prefixes(ip, aaaa_nets):
                    continue
                hostname_hints.setdefault(ip, cand)
                targets.append((ip, "aaaa"))

    # Deduplicate targets — prefer richer source labels later at merge.
    best_source: dict[str, str] = {}
    for ip, source in targets:
        prev = best_source.get(ip)
        if prev is None or _SOURCE_PRIORITY.get(source, 0) > _SOURCE_PRIORITY.get(prev, 0):
            best_source[ip] = source

    observed_map: dict[str, dict[str, Any]] = {}

    def _store_sighting(result: dict[str, Any]) -> None:
        if not result or not _keep_sighting(result):
            return
        ip = result["ip"]
        if ip in neigh_macs:
            result["mac"] = neigh_macs[ip]
        if not result.get("hostname"):
            result["hostname"] = hostname_hints.get(ip)
        result["in_inventory"] = ip in known_norm
        observed_map[ip] = result

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(
                _probe_host,
                ip,
                source=source,
                hostname_hint=hostname_hints.get(ip),
            ): ip
            for ip, source in best_source.items()
        }
        for fut in concurrent.futures.as_completed(futures):
            _store_sighting(fut.result())

    # Second pass: AAAA for PTR names found on IPv4 responders.
    extra_aaaa: dict[str, str | None] = {}
    for row in list(observed_map.values()):
        if row.get("family") != "ipv4":
            continue
        hostname = row.get("hostname")
        if not isinstance(hostname, str) or not hostname.strip():
            continue
        for ip in _aaaa_lookup(hostname):
            if ip in observed_map:
                continue
            if v6_nets and not _ip_in_prefixes(ip, v6_nets):
                continue
            if not v6_nets and aaaa_nets and not _ip_in_prefixes(ip, aaaa_nets):
                continue
            if not v6_nets and not aaaa_nets:
                continue
            extra_aaaa[ip] = hostname
            hostname_hints.setdefault(ip, hostname)

    if extra_aaaa:
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {
                pool.submit(
                    _probe_host,
                    ip,
                    source="aaaa",
                    hostname_hint=hint,
                ): ip
                for ip, hint in extra_aaaa.items()
            }
            for fut in concurrent.futures.as_completed(futures):
                _store_sighting(fut.result())

    # Final hostname enrichment pass (forward DNS map for IPs that lacked PTR).
    for ip, row in observed_map.items():
        if not row.get("hostname"):
            hint = hostname_hints.get(ip)
            if hint:
                row["hostname"] = hint

    observed = _sort_discovered(list(observed_map.values()))
    discovered = _sort_discovered([row for row in observed if not row.get("in_inventory")])
    return {
        "ok": True,
        "scanned_at": scanned_at,
        "subnets": subnets,
        "targets": len(best_source),
        "known_skipped": len(known_norm),
        "observed": observed,
        "observed_count": len(observed),
        "discovered": discovered,
        "count": len(discovered),
        "ipv4_count": sum(1 for d in discovered if d.get("family") == "ipv4"),
        "ipv6_count": sum(1 for d in discovered if d.get("family") == "ipv6"),
        "notes": notes,
    }


_ID_RE = re.compile(r"^srv-[a-z0-9]+(?:-[a-z0-9]+)*$")
_NAME_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SSH_USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,31}$")


def validate_ssh_user(user: str | None) -> str | None:
    """Return an error message if ssh_user is unsafe; None if OK or empty."""
    if user is None:
        return None
    if not isinstance(user, str) or not user.strip():
        return None
    token = user.strip()
    if not _SSH_USER_RE.match(token):
        return "ssh_user must be a plain POSIX username (letters, digits, _-; max 32)"
    return None


def suggest_server_id(ip: str, hostname: str | None = None) -> str:
    if hostname:
        short = hostname.split(".")[0].lower()
        slug = _NAME_SLUG_RE.sub("-", short).strip("-")
        if slug and not slug[0].isdigit():
            candidate = f"srv-{slug}"
            if _ID_RE.match(candidate):
                return candidate
    norm = normalize_ip(ip) or ip
    if _is_ipv6(norm):
        # Compress then make slug-safe: 2001:db8::1 → 2001-db8-0-0-0-0-0-1 style via exploded?
        exploded = ipaddress.IPv6Address(norm).exploded.replace(":", "-")
        return f"srv-host-{exploded}"
    dashed = norm.replace(".", "-")
    return f"srv-host-{dashed}"


def validate_server_id(item_id: str) -> str | None:
    if not item_id or not isinstance(item_id, str):
        return "id is required"
    if not _ID_RE.match(item_id):
        return "id must match srv-<lowercase-slug> (letters, digits, hyphens)"
    return None


def build_server_document(
    *,
    item_id: str,
    name: str,
    ipv4: str | None,
    ipv6: str | None,
    env: str | None,
    status: str,
    hostname: str | None,
    ports: list[int] | None,
    ssh_user: str | None,
    notes: str | None,
    mac: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    if not ipv4 and not ipv6:
        raise ValueError("ipv4 or ipv6 required")
    addresses: dict[str, Any] = {}
    if ipv4:
        addresses["ipv4"] = ipv4
    if ipv6:
        addresses["ipv6"] = ipv6
    mac_n = normalize_mac(mac)
    if mac_n:
        addresses["mac"] = mac_n
    sources = ["network-scan"]
    src = str(source).strip() if isinstance(source, str) and source.strip() else None
    if src and src not in sources:
        sources.append(f"scan:{src}")
    doc: dict[str, Any] = {
        "id": item_id,
        "kind": "server",
        "name": name,
        "status": status,
        "updated": _utc_date(),
        "addresses": addresses,
        "sources": sources,
        "notes": notes or LAN_RESCAN_NOTE,
    }
    if env:
        doc["env"] = env
    if hostname:
        doc["addresses"]["hostname"] = hostname.split(".")[0]
        doc["addresses"]["dns"] = [hostname]
    if ports:
        doc["ports_observed"] = ports
    if ssh_user:
        # Prefer IPv4 for ssh user@host; bracket IPv6 literals.
        if ipv4:
            doc["ssh"] = f"{ssh_user}@{ipv4}"
        elif ipv6:
            doc["ssh"] = f"{ssh_user}@[{ipv6}]"
    return doc
