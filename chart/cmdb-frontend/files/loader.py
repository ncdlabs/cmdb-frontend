"""Load and index CMDB YAML configuration items."""

from __future__ import annotations

import os
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from cmdb_api.addresses import AddressExtractor
from cmdb_api.probe import static_hardware_from_live
from cmdb_api.scan import (
    build_server_document,
    collect_inventory_hostnames,
    collect_known_ips,
    collect_unidentified_ips,
    derive_subnets,
    ip_from_cidr_or_addr,
    ipv4_from_cidr_or_addr,
    ipv6_from_cidr_or_addr,
    suggest_server_id,
    validate_server_id,
    validate_ssh_user,
)

KIND_DIRS = {
    "server": "servers",
    "service": "services",
    "application": "applications",
    "environment": "environments",
}

SEARCH_KEYS = (
    "id",
    "name",
    "notes",
    "owner",
    "ssh",
    "status",
    "kind",
    "env",
)


def default_cmdb_root() -> Path:
    env = os.environ.get("CMDB_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    # Local: repo root may be app parent; inventory/ preferred when present
    repo_root = Path(__file__).resolve().parents[2]
    inventory = repo_root / "inventory"
    if inventory.is_dir() and (inventory / "index.yaml").is_file():
        return inventory
    # Legacy layout: CMDB YAML beside app/ (e.g. ~/cmdb)
    if (repo_root / "index.yaml").is_file():
        return repo_root
    # Running from app/: parent of app is repo or cmdb root
    parent = Path(__file__).resolve().parents[1].parent
    if (parent / "inventory" / "index.yaml").is_file():
        return parent / "inventory"
    if (parent / "index.yaml").is_file():
        return parent
    return repo_root


def _flatten_strings(value: Any, out: list[str]) -> None:
    if value is None:
        return
    if isinstance(value, str):
        out.append(value)
        return
    if isinstance(value, (int, float, bool)):
        out.append(str(value))
        return
    if isinstance(value, dict):
        for v in value.values():
            _flatten_strings(v, out)
        return
    if isinstance(value, (list, tuple, set)):
        for v in value:
            _flatten_strings(v, out)


def _search_blob(item: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in SEARCH_KEYS:
        if key in item:
            _flatten_strings(item[key], parts)
    for key in (
        "addresses",
        "endpoints",
        "roles",
        "sources",
        "depends_on",
        "ports",
        "os",
        "hardware",
        "runtime",
        "runs_on",
        "k8s",
    ):
        if key in item:
            _flatten_strings(item[key], parts)
    if "path" in item:
        parts.append(str(item["path"]))
    return " ".join(parts).lower()


def _as_id_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if v is not None and str(v).strip()]


def _address_candidates(item: dict[str, Any]) -> list[str]:
    """Flatten address-like fields for substring / exact matching."""
    out: list[str] = []
    addresses = item.get("addresses")
    if isinstance(addresses, dict):
        for key in ("ipv4", "ipv6", "hostname", "tailscale"):
            val = addresses.get(key)
            if isinstance(val, str) and val.strip():
                out.append(val.strip())
        dns = addresses.get("dns")
        if isinstance(dns, list):
            for entry in dns:
                if isinstance(entry, str) and entry.strip():
                    out.append(entry.strip())
        elif isinstance(dns, str) and dns.strip():
            out.append(dns.strip())
    host = AddressExtractor.ssh_host(item.get("ssh"))
    if host:
        out.append(host)
    name = item.get("name")
    if isinstance(name, str) and name.strip():
        out.append(name.strip())
    return out


def _item_runtime(item: dict[str, Any]) -> str | None:
    runtime = str(item.get("runtime") or "").strip().lower()
    if runtime:
        return runtime
    if isinstance(item.get("k8s"), dict):
        return "k3s"
    return None


class CmdbStore:
    def __init__(self, root: Path | None = None, refresh_seconds: float = 30.0) -> None:
        self.root = root or default_cmdb_root()
        self.refresh_seconds = refresh_seconds
        self._lock = threading.RLock()
        self._items: dict[str, dict[str, Any]] = {}
        self._dependents: dict[str, list[str]] = {}
        self._load_errors: list[dict[str, str]] = []
        self._loaded_at: float | None = None
        self._index_updated: str | None = None
        self.reload()

    def maybe_refresh(self) -> None:
        if self._loaded_at is None:
            self.reload()
            return
        if time.monotonic() - self._loaded_at >= self.refresh_seconds:
            self.reload()

    def reload(self) -> None:
        items: dict[str, dict[str, Any]] = {}
        errors: list[dict[str, str]] = []
        index_updated: str | None = None

        index_path = self.root / "index.yaml"
        if index_path.is_file():
            try:
                index_data = yaml.safe_load(index_path.read_text()) or {}
                if isinstance(index_data, dict):
                    index_updated = str(index_data.get("updated") or "") or None
            except Exception as exc:  # noqa: BLE001 — surface parse failures
                errors.append({"path": str(index_path.relative_to(self.root)), "error": str(exc)})

        for kind, dirname in KIND_DIRS.items():
            directory = self.root / dirname
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.yaml")):
                # Skip AppleDouble / hidden junk (._file.yaml from macOS archives)
                if path.name.startswith(".") or path.name.startswith("._"):
                    continue
                rel = str(path.relative_to(self.root))
                try:
                    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                    if not isinstance(data, dict):
                        errors.append({"path": rel, "error": "root is not a mapping"})
                        continue
                    item_id = str(data.get("id") or path.stem)
                    data = dict(data)
                    data.setdefault("id", item_id)
                    data.setdefault("kind", kind)
                    data["path"] = rel
                    data["_search"] = _search_blob(data)
                    if item_id in items:
                        prev = items[item_id]
                        errors.append(
                            {
                                "path": rel,
                                "error": (
                                    f"Duplicate CI id '{item_id}' "
                                    f"(also defined in {prev.get('path')})"
                                ),
                            }
                        )
                        continue
                    items[item_id] = data
                except Exception as exc:  # noqa: BLE001
                    errors.append({"path": rel, "error": str(exc)})

        dependents: dict[str, list[str]] = {item_id: [] for item_id in items}
        for item_id, item in items.items():
            deps = item.get("depends_on") or []
            if not isinstance(deps, list):
                continue
            for dep in deps:
                dep_id = str(dep)
                dependents.setdefault(dep_id, []).append(item_id)

        for dep_id in dependents:
            dependents[dep_id] = sorted(set(dependents[dep_id]))

        with self._lock:
            self._items = items
            self._dependents = dependents
            self._load_errors = errors
            self._index_updated = index_updated
            self._loaded_at = time.monotonic()

    def meta(self) -> dict[str, Any]:
        with self._lock:
            counts: dict[str, int] = {}
            statuses: dict[str, int] = {}
            envs: dict[str, int] = {}
            for item in self._items.values():
                kind = str(item.get("kind") or "unknown")
                status = str(item.get("status") or "unknown")
                env = str(item.get("env") or "")
                counts[kind] = counts.get(kind, 0) + 1
                statuses[status] = statuses.get(status, 0) + 1
                if env:
                    envs[env] = envs.get(env, 0) + 1
            return {
                "root": str(self.root),
                "total": len(self._items),
                "counts": counts,
                "statuses": statuses,
                "envs": sorted(envs.keys()),
                "index_updated": self._index_updated,
                "load_errors": list(self._load_errors),
                "error_count": len(self._load_errors),
            }

    def list_items(
        self,
        q: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        env: str | None = None,
        role: str | None = None,
        runtime: str | None = None,
    ) -> list[dict[str, Any]]:
        needle = (q or "").strip().lower()
        role_needle = (role or "").strip().lower()
        runtime_needle = (runtime or "").strip().lower()
        with self._lock:
            results: list[dict[str, Any]] = []
            for item in self._items.values():
                if kind and str(item.get("kind")) != kind:
                    continue
                if status and str(item.get("status")) != status:
                    continue
                if env and str(item.get("env") or "") != env:
                    continue
                if role_needle:
                    roles = item.get("roles") or []
                    if not isinstance(roles, list):
                        continue
                    role_set = {str(r).strip().lower() for r in roles if r is not None}
                    if role_needle not in role_set:
                        continue
                if runtime_needle:
                    item_rt = _item_runtime(item)
                    if item_rt != runtime_needle:
                        continue
                if needle and needle not in item.get("_search", ""):
                    continue
                results.append(self._summary(item))
            results.sort(key=lambda row: (str(row.get("kind") or ""), str(row.get("id") or "")))
            return results

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._items.get(item_id)
            if not item:
                return None
            detail = {k: v for k, v in item.items() if not k.startswith("_")}
            deps = detail.get("depends_on") or []
            resolved_deps = []
            if isinstance(deps, list):
                for dep in deps:
                    dep_id = str(dep)
                    dep_item = self._items.get(dep_id)
                    resolved_deps.append(
                        self._summary(dep_item) if dep_item else {"id": dep_id, "missing": True}
                    )
            dependent_ids = self._dependents.get(item_id, [])
            resolved_dependents = [
                self._summary(self._items[dep_id])
                for dep_id in dependent_ids
                if dep_id in self._items
            ]
            detail["depends_on_resolved"] = resolved_deps
            detail["dependents"] = resolved_dependents
            if str(detail.get("kind") or "") == "application":
                detail["placement"] = self._placement(detail)
            return detail

    def by_address(self, q: str) -> list[dict[str, Any]]:
        """Match servers by IPv4, hostname, Tailscale IP, DNS alias, or SSH host."""
        needle = (q or "").strip().lower()
        if not needle:
            return []
        with self._lock:
            results: list[dict[str, Any]] = []
            for item in self._items.values():
                if str(item.get("kind") or "") != "server":
                    continue
                candidates = [c.lower() for c in _address_candidates(item)]
                if any(needle == c or needle in c for c in candidates):
                    summary = self._summary(item)
                    summary["match_fields"] = [
                        c for c in _address_candidates(item) if needle in c.lower()
                    ]
                    results.append(summary)
            results.sort(key=lambda row: str(row.get("id") or ""))
            return results

    def by_role(self, role: str) -> list[dict[str, Any]]:
        """Servers that declare the given role."""
        return self.list_items(kind="server", role=role)

    def network_scan_context(self) -> dict[str, Any]:
        """Subnets + known IPs for a one-shot LAN rescan."""
        with self._lock:
            items = list(self._items.values())
        subnets = derive_subnets(items)
        known = collect_known_ips(items)
        unidentified = collect_unidentified_ips(items)
        hostnames = collect_inventory_hostnames(items)
        return {
            "subnets": subnets,
            "known_ips": sorted(known),
            "unidentified_ips": sorted(unidentified),
            "hostnames": sorted(hostnames),
            "writable": self.inventory_writable(),
            "root": str(self.root),
        }

    def inventory_writable(self) -> bool:
        servers = self.root / "servers"
        try:
            if not servers.is_dir():
                return False
            probe = servers / ".cmdb-write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return True
        except OSError:
            return False

    def apply_observed_specs(self, item_id: str, live: dict[str, Any]) -> dict[str, Any]:
        """Write hardware/os/network from a successful probe into server YAML, then reload.

        Does not change ``status`` (lifecycle). Live gauges (load, used RAM, temp)
        are intentionally not stored — only static observed specs.
        """
        if not self.inventory_writable():
            raise PermissionError(
                f"Inventory is not writable at {self.root} "
                "(need a writable PVC/bind-mount for /data/cmdb)"
            )
        if not isinstance(live, dict) or not live:
            raise ValueError("live probe payload is empty")

        with self._lock:
            item = self._items.get(item_id)
            if not item:
                raise KeyError(item_id)
            if item.get("kind") != "server":
                raise ValueError("Observed specs can only be applied to servers")
            rel = str(item.get("path") or "")
            if not rel or rel.startswith("/") or ".." in Path(rel).parts:
                raise ValueError(f"Server has no safe inventory path: {item_id}")

        path = (self.root / rel).resolve()
        if not path.is_file() or not str(path).startswith(str(self.root.resolve())):
            raise FileNotFoundError(f"Inventory file missing: {rel}")

        blocks = static_hardware_from_live(live)
        observed_at = (
            datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        )
        hardware = blocks.get("hardware") or {}
        for disk in hardware.get("disks") or []:
            if isinstance(disk, dict):
                disk.setdefault("source", "ssh-probe")
                disk["observed_at"] = observed_at
                disk.setdefault("confidence", "high")
        for fs in hardware.get("filesystems") or []:
            if isinstance(fs, dict):
                fs.setdefault("source", "ssh-probe")
                fs["observed_at"] = observed_at
        network = blocks.get("network")
        if isinstance(network, dict):
            for iface in network.get("interfaces") or []:
                if isinstance(iface, dict):
                    iface.setdefault("source", "ssh-probe")
                    iface["observed_at"] = observed_at
                    iface.setdefault("confidence", "high")

        fields: list[str] = []
        with self._lock:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(raw, dict):
                raise ValueError(f"Inventory root is not a mapping: {rel}")
            doc = dict(raw)
            if hardware:
                doc["hardware"] = hardware
                fields.append("hardware")
            os_block = blocks.get("os") or {}
            if os_block:
                doc["os"] = os_block
                fields.append("os")
            if network:
                doc["network"] = network
                fields.append("network")
            if not fields:
                raise ValueError("Probe produced no hardware/os/network to persist")
            doc["updated"] = date.today().isoformat()
            fields.append("updated")
            with path.open("w", encoding="utf-8") as handle:
                yaml.safe_dump(doc, handle, sort_keys=False, default_flow_style=False)
            self.reload()

        return {
            "id": item_id,
            "path": rel,
            "updated": doc["updated"],
            "observed_at": observed_at,
            "fields": fields,
        }

    def create_servers_from_scan(self, entries: list[dict[str, Any]]) -> dict[str, Any]:
        """Write new server YAML files from LAN-scan selections, then reload."""
        if not entries:
            raise ValueError("No devices selected")
        if not self.inventory_writable():
            raise PermissionError(
                f"Inventory is not writable at {self.root} "
                "(need a writable PVC/bind-mount for /data/cmdb)"
            )

        created: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        servers_dir = self.root / "servers"

        with self._lock:
            existing_ids = set(self._items.keys())
            known_ips = collect_known_ips(self._items.values())

        for raw in entries:
            if not isinstance(raw, dict):
                errors.append({"error": "Invalid entry (expected object)"})
                continue
            raw_ip = str(raw.get("ip") or raw.get("ipv4") or raw.get("ipv6") or "").strip()
            ipv4 = ipv4_from_cidr_or_addr(raw.get("ipv4") or (raw_ip if ":" not in raw_ip else None))
            ipv6 = ipv6_from_cidr_or_addr(raw.get("ipv6") or (raw_ip if ":" in raw_ip else None))
            # Also accept unified `ip` field.
            if not ipv4 and not ipv6:
                parsed = ip_from_cidr_or_addr(raw_ip)
                if parsed and ":" not in parsed:
                    ipv4 = parsed
                elif parsed:
                    ipv6 = parsed
            if not ipv4 and not ipv6:
                errors.append({"ip": raw_ip or "?", "error": "Invalid or missing IPv4/IPv6"})
                continue
            if (ipv4 and ipv4 in known_ips) or (ipv6 and ipv6 in known_ips):
                errors.append({"ip": ipv4 or ipv6 or "?", "error": "IP already present in inventory"})
                continue

            hostname = raw.get("hostname")
            hostname_s = str(hostname).strip() if isinstance(hostname, str) and hostname.strip() else None
            primary = ipv4 or ipv6 or raw_ip
            item_id = str(raw.get("id") or "").strip() or suggest_server_id(primary, hostname_s)
            id_err = validate_server_id(item_id)
            if id_err:
                errors.append({"ip": primary, "id": item_id, "error": id_err})
                continue
            if item_id in existing_ids:
                errors.append({"ip": primary, "id": item_id, "error": f"CI id already exists: {item_id}"})
                continue

            name = str(raw.get("name") or "").strip() or (hostname_s.split(".")[0] if hostname_s else primary)
            env = raw.get("env")
            env_s = str(env).strip() if isinstance(env, str) and env.strip() else None
            status = str(raw.get("status") or "unknown").strip() or "unknown"
            if status not in {"active", "deprecated", "planned", "unknown"}:
                errors.append({"ip": primary, "id": item_id, "error": f"Invalid status: {status}"})
                continue

            ports_raw = raw.get("ports") or []
            ports = [
                int(p)
                for p in ports_raw
                if isinstance(p, (int, float, str))
                and str(p).isdigit()
                and 1 <= int(p) <= 65535
            ]
            ssh_user = raw.get("ssh_user")
            default_ssh = os.environ.get("CMDB_DEFAULT_SSH_USER", "").strip()
            ssh_user_s = (
                str(ssh_user).strip()
                if isinstance(ssh_user, str) and ssh_user.strip()
                else (default_ssh if default_ssh and 22 in ports else None)
            )
            ssh_err = validate_ssh_user(ssh_user_s)
            if ssh_err:
                errors.append({"ip": primary, "id": item_id, "error": ssh_err})
                continue
            notes = raw.get("notes")
            notes_s = str(notes).strip() if isinstance(notes, str) and notes.strip() else None
            if notes_s and len(notes_s) > 2000:
                errors.append({"ip": primary, "id": item_id, "error": "notes too long (max 2000)"})
                continue
            if len(name) > 128:
                errors.append({"ip": primary, "id": item_id, "error": "name too long (max 128)"})
                continue

            doc = build_server_document(
                item_id=item_id,
                name=name,
                ipv4=ipv4,
                ipv6=ipv6,
                env=env_s,
                status=status,
                hostname=hostname_s,
                ports=ports or None,
                ssh_user=ssh_user_s,
                notes=notes_s,
            )
            path = servers_dir / f"{item_id}.yaml"
            try:
                # Exclusive create — refuse if another request raced us.
                with path.open("x", encoding="utf-8") as handle:
                    yaml.safe_dump(doc, handle, sort_keys=False, default_flow_style=False)
            except FileExistsError:
                errors.append(
                    {"ip": primary, "id": item_id, "error": f"File already exists: {path.name}"}
                )
                continue
            except OSError as exc:
                errors.append({"ip": primary, "id": item_id, "error": str(exc)})
                continue

            existing_ids.add(item_id)
            if ipv4:
                known_ips.add(ipv4)
            if ipv6:
                known_ips.add(ipv6)
            created.append({"id": item_id, "ip": primary, "path": f"servers/{item_id}.yaml"})

        if created:
            self.reload()

        return {
            "ok": len(errors) == 0,
            "created": created,
            "created_count": len(created),
            "errors": errors,
            "error_count": len(errors),
        }

    def context_bundle(self, item_id: str) -> dict[str, Any] | None:
        """One-shot CI + deps + dependents + related servers (+ placement for apps)."""
        detail = self.get_item(item_id)
        if not detail:
            return None

        with self._lock:
            related_server_ids: list[str] = []
            kind = str(detail.get("kind") or "")
            if kind == "server":
                related_server_ids = [item_id]
            elif kind == "application":
                related_server_ids = self._collect_server_ids(detail)
            else:
                related_server_ids = self._collect_server_ids(detail)

            # Also include servers from dependents / depends when useful
            for dep in detail.get("depends_on_resolved") or []:
                if isinstance(dep, dict) and dep.get("kind") == "server" and dep.get("id"):
                    related_server_ids.append(str(dep["id"]))
            for dep in detail.get("dependents") or []:
                if isinstance(dep, dict) and dep.get("kind") == "server" and dep.get("id"):
                    related_server_ids.append(str(dep["id"]))

            related_server_ids = list(dict.fromkeys(related_server_ids))
            related_servers = [self._resolve_summary(sid) for sid in related_server_ids]

            placement = detail.get("placement") if kind == "application" else None
            if placement is None and kind == "application":
                placement = self._placement(detail)

            return {
                "id": item_id,
                "ci": detail,
                "depends_on": detail.get("depends_on_resolved") or [],
                "dependents": detail.get("dependents") or [],
                "placement": placement,
                "related_servers": related_servers,
            }

    @staticmethod
    def format_context_markdown(bundle: dict[str, Any]) -> str:
        """LLM-friendly markdown for a context_bundle result."""
        ci = bundle.get("ci") or {}
        lines: list[str] = []
        lines.append(f"# {ci.get('id') or bundle.get('id')}")
        lines.append("")
        lines.append(f"- **kind**: {ci.get('kind')}")
        lines.append(f"- **name**: {ci.get('name')}")
        lines.append(f"- **status**: {ci.get('status')}")
        if ci.get("env"):
            lines.append(f"- **env**: {ci.get('env')}")
        if ci.get("owner"):
            lines.append(f"- **owner**: {ci.get('owner')}")
        if ci.get("ssh"):
            lines.append(f"- **ssh**: `{ci.get('ssh')}`")
        addresses = ci.get("addresses")
        if isinstance(addresses, dict) and addresses:
            addr_bits = ", ".join(f"{k}={v}" for k, v in addresses.items() if not isinstance(v, (list, dict)))
            if addr_bits:
                lines.append(f"- **addresses**: {addr_bits}")
        roles = ci.get("roles")
        if isinstance(roles, list) and roles:
            lines.append(f"- **roles**: {', '.join(str(r) for r in roles)}")
        endpoints = ci.get("endpoints")
        if isinstance(endpoints, list) and endpoints:
            lines.append(f"- **endpoints**: {', '.join(str(e) for e in endpoints)}")
        if ci.get("notes"):
            lines.append(f"- **notes**: {ci.get('notes')}")
        lines.append("")

        k3s = ci.get("k3s")
        if isinstance(k3s, dict):
            lines.append("## k3s membership")
            if k3s.get("cluster"):
                lines.append(f"- **cluster**: `{k3s.get('cluster')}`")
            desired = k3s.get("desired") if isinstance(k3s.get("desired"), dict) else {}
            observed = k3s.get("observed") if isinstance(k3s.get("observed"), dict) else {}
            if desired:
                lines.append(
                    "- **desired**: "
                    + ", ".join(
                        f"{k}={desired[k]}"
                        for k in ("node_name", "role", "etcd", "schedulable")
                        if k in desired
                    )
                )
            if observed:
                bits = [
                    f"{k}={observed[k]}"
                    for k in ("ready", "role", "etcd", "schedulable", "k3s_version", "kubernetes_version")
                    if k in observed
                ]
                if observed.get("joined_at"):
                    bits.append(f"joined_at={observed['joined_at']}")
                if observed.get("observed_at"):
                    bits.append(f"observed_at={observed['observed_at']}")
                if bits:
                    lines.append("- **observed**: " + ", ".join(bits))
            lines.append("")

        hardware = ci.get("hardware") if isinstance(ci.get("hardware"), dict) else {}
        disks = hardware.get("disks") if isinstance(hardware.get("disks"), list) else []
        if disks:
            lines.append("## Physical disks")
            for d in disks:
                if not isinstance(d, dict):
                    continue
                lines.append(
                    "- "
                    + " · ".join(
                        str(x)
                        for x in (
                            d.get("device"),
                            d.get("capacity_human"),
                            d.get("interface"),
                            d.get("model"),
                            d.get("purpose"),
                        )
                        if x
                    )
                )
            lines.append("")

        filesystems = hardware.get("filesystems") if isinstance(hardware.get("filesystems"), list) else []
        if not filesystems and isinstance(hardware.get("storage"), list):
            filesystems = hardware["storage"]
        if filesystems:
            lines.append("## Filesystems")
            for fs in filesystems:
                if not isinstance(fs, dict):
                    continue
                mount = fs.get("mount") or fs.get("source") or "?"
                size = fs.get("size_human") or fs.get("size_bytes") or "?"
                device = fs.get("device") or fs.get("source") or ""
                lines.append(f"- `{mount}`: {size}" + (f" ({device})" if device and device != mount else ""))
            lines.append("")

        network = ci.get("network") if isinstance(ci.get("network"), dict) else {}
        ifaces = network.get("interfaces") if isinstance(network.get("interfaces"), list) else []
        if ifaces:
            lines.append("## Network interfaces")
            for nic in ifaces:
                if not isinstance(nic, dict):
                    continue
                bits = [
                    nic.get("name"),
                    f"{nic['speed_mbps']} Mb/s" if nic.get("speed_mbps") is not None else None,
                    nic.get("duplex"),
                    nic.get("ipv4"),
                    nic.get("mac"),
                ]
                lines.append("- " + " · ".join(str(b) for b in bits if b))
            lines.append("")

        storage = ci.get("storage") if isinstance(ci.get("storage"), dict) else None
        if storage:
            lines.append("## Kubernetes storage")
            obs = storage.get("observed") if isinstance(storage.get("observed"), dict) else storage
            if obs.get("provider"):
                lines.append(f"- **provider**: {obs.get('provider')}")
            if obs.get("default_storage_class"):
                lines.append(f"- **default StorageClass**: {obs.get('default_storage_class')}")
            scs = obs.get("storage_classes") if isinstance(obs.get("storage_classes"), list) else []
            for sc in scs:
                if isinstance(sc, dict) and sc.get("name"):
                    lines.append(
                        f"- SC `{sc.get('name')}` provisioner={sc.get('provisioner')} "
                        f"default={sc.get('is_default')}"
                    )
            if obs.get("observed_at"):
                lines.append(f"- **observed_at**: {obs.get('observed_at')}")
            lines.append("")

        cluster = ci.get("cluster") if isinstance(ci.get("cluster"), dict) else None
        if cluster:
            lines.append("## Cluster")
            if cluster.get("name"):
                lines.append(f"- **name**: {cluster.get('name')}")
            obs = cluster.get("observed") if isinstance(cluster.get("observed"), dict) else {}
            nodes = obs.get("nodes") if isinstance(obs.get("nodes"), list) else []
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                lines.append(
                    "- "
                    + " · ".join(
                        str(x)
                        for x in (
                            node.get("node_name"),
                            node.get("server_id"),
                            f"role={node.get('role')}" if node.get("role") else None,
                            f"etcd={node.get('etcd')}" if "etcd" in node else None,
                            f"ready={node.get('ready')}" if "ready" in node else None,
                            node.get("arch"),
                        )
                        if x is not None and x != ""
                    )
                )
            lines.append("")

        deps = bundle.get("depends_on") or []
        lines.append("## Depends on")
        if deps:
            for dep in deps:
                if not isinstance(dep, dict):
                    continue
                miss = " (missing)" if dep.get("missing") else ""
                lines.append(f"- `{dep.get('id')}` ({dep.get('kind') or '?'}){miss}")
        else:
            lines.append("- (none)")
        lines.append("")

        dependents = bundle.get("dependents") or []
        lines.append("## Dependents")
        if dependents:
            for dep in dependents:
                if not isinstance(dep, dict):
                    continue
                lines.append(f"- `{dep.get('id')}` ({dep.get('kind') or '?'})")
        else:
            lines.append("- (none)")
        lines.append("")

        placement = bundle.get("placement")
        if isinstance(placement, dict):
            lines.append("## Placement")
            lines.append(f"- **runtime**: {placement.get('runtime')}")
            servers = placement.get("servers") or []
            if servers:
                lines.append(
                    "- **servers**: "
                    + ", ".join(f"`{s.get('id')}`" for s in servers if isinstance(s, dict))
                )
            services = placement.get("services") or []
            if services:
                lines.append(
                    "- **services**: "
                    + ", ".join(f"`{s.get('id')}`" for s in services if isinstance(s, dict))
                )
            k8s = placement.get("k8s")
            if isinstance(k8s, dict) and k8s:
                ns = k8s.get("namespace")
                if ns:
                    lines.append(f"- **k8s.namespace**: {ns}")
            lines.append("")

        related = bundle.get("related_servers") or []
        if related:
            lines.append("## Related servers")
            for srv in related:
                if not isinstance(srv, dict):
                    continue
                addr = srv.get("addresses") or {}
                ipv4 = addr.get("ipv4") if isinstance(addr, dict) else None
                extra = f" — {ipv4}" if ipv4 else ""
                lines.append(f"- `{srv.get('id')}`{extra}")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    def _resolve_summary(self, item_id: str) -> dict[str, Any]:
        item = self._items.get(item_id)
        if item:
            return self._summary(item)
        return {"id": item_id, "missing": True}

    def _collect_server_ids(self, item: dict[str, Any]) -> list[str]:
        """Servers this CI runs on: explicit runs_on, else depends_on servers (+ via services)."""
        explicit = _as_id_list(item.get("runs_on"))
        if explicit:
            return list(dict.fromkeys(explicit))

        found: list[str] = []
        seen: set[str] = set()

        def add_server(server_id: str) -> None:
            if server_id in seen:
                return
            seen.add(server_id)
            found.append(server_id)

        for dep_id in _as_id_list(item.get("depends_on")):
            dep = self._items.get(dep_id)
            if not dep:
                if dep_id.startswith("srv-"):
                    add_server(dep_id)
                continue
            kind = str(dep.get("kind") or "")
            if kind == "server":
                add_server(dep_id)
            elif kind == "service":
                for nested_id in _as_id_list(dep.get("depends_on")):
                    nested = self._items.get(nested_id)
                    if nested and str(nested.get("kind") or "") == "server":
                        add_server(nested_id)
                    elif nested_id.startswith("srv-"):
                        add_server(nested_id)
        return found

    def _placement(self, detail: dict[str, Any]) -> dict[str, Any]:
        server_ids = self._collect_server_ids(detail)
        servers = [self._resolve_summary(sid) for sid in server_ids]

        service_ids: list[str] = []
        for dep_id in _as_id_list(detail.get("depends_on")):
            dep = self._items.get(dep_id)
            if dep and str(dep.get("kind") or "") == "service":
                service_ids.append(dep_id)
            elif not dep and dep_id.startswith("svc-"):
                service_ids.append(dep_id)
        services = [self._resolve_summary(sid) for sid in service_ids]

        k8s_raw = detail.get("k8s")
        k8s = k8s_raw if isinstance(k8s_raw, dict) else None
        runtime = str(detail.get("runtime") or "").strip().lower() or None
        if not runtime and k8s:
            runtime = "k3s"
        if not runtime:
            runtime = "host"

        return {
            "runtime": runtime,
            "servers": servers,
            "services": services,
            "k8s": k8s,
        }

    @staticmethod
    def _placement_summary(item: dict[str, Any]) -> str | None:
        if str(item.get("kind") or "") != "application":
            return None
        runtime = str(item.get("runtime") or "").strip().lower()
        k8s = item.get("k8s") if isinstance(item.get("k8s"), dict) else None
        if runtime == "k3s" or k8s:
            ns = str((k8s or {}).get("namespace") or "").strip()
            return f"k3s/{ns}" if ns else "k3s"
        runs_on = _as_id_list(item.get("runs_on"))
        if runs_on:
            return ", ".join(runs_on[:2]) + ("…" if len(runs_on) > 2 else "")
        servers = [d for d in _as_id_list(item.get("depends_on")) if d.startswith("srv-")]
        if servers:
            return ", ".join(servers[:2]) + ("…" if len(servers) > 2 else "")
        return None

    @staticmethod
    def _summary(item: dict[str, Any]) -> dict[str, Any]:
        summary = {
            "id": item.get("id"),
            "kind": item.get("kind"),
            "name": item.get("name"),
            "status": item.get("status"),
            "env": item.get("env"),
            "path": item.get("path"),
            "owner": item.get("owner"),
            "updated": item.get("updated"),
        }
        endpoints = item.get("endpoints")
        if isinstance(endpoints, list) and endpoints:
            summary["endpoints"] = endpoints[:3]
        addresses = item.get("addresses")
        if isinstance(addresses, dict):
            filtered = {
                k: addresses[k]
                for k in ("ipv4", "ipv6", "hostname", "tailscale")
                if k in addresses
            }
            if filtered:
                summary["addresses"] = filtered
        placement_summary = CmdbStore._placement_summary(item)
        if placement_summary:
            summary["placement_summary"] = placement_summary
        runtime = str(item.get("runtime") or "").strip().lower()
        if runtime:
            summary["runtime"] = runtime
        elif isinstance(item.get("k8s"), dict):
            summary["runtime"] = "k3s"
        ssh = item.get("ssh")
        if isinstance(ssh, str) and ssh.strip():
            summary["ssh"] = ssh.strip()
        return summary
