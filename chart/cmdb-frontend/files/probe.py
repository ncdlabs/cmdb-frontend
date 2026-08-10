"""One-shot SSH host probe for server machine specs and live metrics."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REMOTE_SCRIPT = r"""
set +e
emit() { printf '%s\n' "$1"; }

if [ -r /etc/os-release ]; then . /etc/os-release; fi
emit "OS_NAME=${NAME:-}"
emit "OS_VERSION=${VERSION_ID:-}"
emit "OS_CODENAME=${VERSION_CODENAME:-${VERSION_CODENAME:-}}"
emit "ARCH=$(uname -m 2>/dev/null)"
emit "KERNEL=$(uname -r 2>/dev/null)"
emit "HOSTNAME=$(hostname 2>/dev/null)"

CPU_MODEL=$(grep -m1 -E 'model name|Model|Hardware' /proc/cpuinfo 2>/dev/null | cut -d: -f2- | sed 's/^ //')
if [ -z "$CPU_MODEL" ] && command -v lscpu >/dev/null 2>&1; then
  CPU_MODEL=$(lscpu 2>/dev/null | awk -F: '/Model name|Model/{gsub(/^[ \t]+/,"",$2); print $2; exit}')
fi
emit "CPU_MODEL=${CPU_MODEL:-}"
emit "CPU_CORES=$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo)"
THREADS=$(grep -c '^processor' /proc/cpuinfo 2>/dev/null)
emit "CPU_THREADS=${THREADS:-}"

emit "MEM_TOTAL_KB=$(awk '/MemTotal/{print $2}' /proc/meminfo 2>/dev/null)"
emit "MEM_AVAIL_KB=$(awk '/MemAvailable/{print $2}' /proc/meminfo 2>/dev/null)"
emit "MEM_FREE_KB=$(awk '/MemFree/{print $2}' /proc/meminfo 2>/dev/null)"
emit "SWAP_TOTAL_KB=$(awk '/SwapTotal/{print $2}' /proc/meminfo 2>/dev/null)"
emit "SWAP_FREE_KB=$(awk '/SwapFree/{print $2}' /proc/meminfo 2>/dev/null)"

emit "LOADAVG=$(cut -d' ' -f1-3 /proc/loadavg 2>/dev/null)"
emit "UPTIME_SEC=$(cut -d. -f1 /proc/uptime 2>/dev/null)"

if command -v vcgencmd >/dev/null 2>&1; then
  PT=$(vcgencmd measure_temp 2>/dev/null | tr -dc '0-9.')
  [ -n "$PT" ] && emit "TEMP|rpizero|$PT"
fi
for z in /sys/class/thermal/thermal_zone*/temp; do
  [ -f "$z" ] || continue
  raw=$(cat "$z" 2>/dev/null) || continue
  typ=$(cat "$(dirname "$z")/type" 2>/dev/null || echo thermal)
  # millidegrees C on Linux thermal zones
  if [ "$raw" -gt 1000 ] 2>/dev/null; then
    c=$((raw / 1000))
  else
    c=$raw
  fi
  emit "TEMP|$typ|$c"
done
if command -v sensors >/dev/null 2>&1; then
  sensors -u 2>/dev/null | awk '
    /^[A-Za-z0-9].*:$/ { gsub(/:$/,"",$0); name=$0 }
    /_input:/ {
      for (i=1;i<=NF;i++) if ($i ~ /_input:/) {
        key=$i; gsub(/:$/,"",key); val=$(i+1);
        if (key ~ /temp/ && val+0 == val) print "TEMP|" name "|" val
      }
    }'
fi

if command -v df >/dev/null 2>&1; then
  df -B1 --output=source,target,size,used,avail,pcent -x tmpfs -x devtmpfs -x squashfs -x overlay -x efivarfs 2>/dev/null | tail -n +2 | while read -r src tgt size used avail pct; do
    [ -n "$src" ] || continue
    case "$tgt" in
      /proc|/proc/*|/sys|/sys/*|/dev|/dev/*|/run|/run/*) continue ;;
    esac
    emit "DISK|$src|$tgt|$size|$used|$avail|${pct%%%}"
  done
fi

# Block device inventory for drive type / model (joined to mounts in Python)
if command -v lsblk >/dev/null 2>&1; then
  # NAME PATH TYPE ROTA TRAN MODEL SERIAL SIZE PKNAME RM HOTPLUG VENDOR
  lsblk -P -b -o NAME,PATH,TYPE,ROTA,TRAN,MODEL,SERIAL,SIZE,PKNAME,RM,HOTPLUG,VENDOR,REV 2>/dev/null | while IFS= read -r line; do
    [ -n "$line" ] || continue
    emit "BLK|$line"
  done
  # Physical disks only (TYPE=disk)
  lsblk -P -b -d -o NAME,PATH,TYPE,ROTA,TRAN,MODEL,SERIAL,SIZE,RM,HOTPLUG,VENDOR,REV 2>/dev/null | while IFS= read -r line; do
    [ -n "$line" ] || continue
    echo "$line" | grep -q 'TYPE="disk"' || continue
    emit "PHYSDISK|$line"
  done
fi
# Fallback hints from sysfs when lsblk lacks TRAN
for d in /sys/block/*; do
  [ -d "$d" ] || continue
  name=$(basename "$d")
  case "$name" in
    loop*|ram*|dm-*|md*|zram*) continue ;;
  esac
  rota=$(cat "$d/queue/rotational" 2>/dev/null || echo)
  model=$(tr -d '\\0\\n' <"$d/device/model" 2>/dev/null || true)
  vendor=$(tr -d '\\0\\n' <"$d/device/vendor" 2>/dev/null || true)
  emit "SYSBLK|$name|rota=${rota}|model=${model}|vendor=${vendor}"
done

# Physical NICs: negotiated speed / duplex / mtu / mac / ipv4
for iface in /sys/class/net/*; do
  [ -d "$iface" ] || continue
  name=$(basename "$iface")
  case "$name" in
    lo|docker*|cni*|flannel*|veth*|br-*|virbr*|tailscale*|kube-*|cali*|tunl*|sit*|ip6tnl*) continue ;;
  esac
  # Skip virtual device types
  if [ -d "$iface/device" ] || [ -e "$iface/device" ]; then
    :
  else
    # still allow eth*/en*/wl* without device symlink on some boards
    case "$name" in
      eth*|en*|wl*|wlan*) ;;
      *) continue ;;
    esac
  fi
  mac=$(cat "$iface/address" 2>/dev/null || echo)
  mtu=$(cat "$iface/mtu" 2>/dev/null || echo)
  oper=$(cat "$iface/operstate" 2>/dev/null || echo)
  speed=$(cat "$iface/speed" 2>/dev/null || echo)
  duplex=$(cat "$iface/duplex" 2>/dev/null || echo)
  if command -v ethtool >/dev/null 2>&1; then
    et=$(ethtool "$name" 2>/dev/null)
    es=$(printf '%s\n' "$et" | awk -F': ' '/Speed:/{print $2}' | head -1 | tr -dc '0-9')
    ed=$(printf '%s\n' "$et" | awk -F': ' '/Duplex:/{print tolower($2)}' | head -1)
    [ -n "$es" ] && speed=$es
    [ -n "$ed" ] && duplex=$ed
  fi
  ipv4=$(ip -4 -o addr show dev "$name" 2>/dev/null | awk '{print $4}' | head -1)
  emit "NIC|$name|mac=${mac}|mtu=${mtu}|operstate=${oper}|speed_mbps=${speed}|duplex=${duplex}|ipv4=${ipv4}"
done

# Product / board hints
if [ -r /sys/devices/virtual/dmi/id/product_name ]; then
  emit "PRODUCT=$(cat /sys/devices/virtual/dmi/id/product_name 2>/dev/null)"
  emit "VENDOR=$(cat /sys/devices/virtual/dmi/id/sys_vendor 2>/dev/null)"
fi
if [ -r /proc/device-tree/model ]; then
  emit "BOARD=$(tr -d '\\0' </proc/device-tree/model 2>/dev/null)"
fi
"""


# user@host, host, user@[ipv6], [ipv6] — no shell metacharacters / ProxyJump flags
_SSH_DEST_RE = re.compile(
    r"^(?:[A-Za-z0-9._+-]+@)?"
    r"(?:\[[0-9A-Fa-f:]+\]|[A-Za-z0-9._-]+)$"
)
_PROBE_ERROR_MAX = 400
_PROBE_TIMEOUT_DEFAULT = 20
_PROBE_TIMEOUT_MIN = 5
_PROBE_TIMEOUT_MAX = 60


def _known_hosts_file() -> str:
    """Private known_hosts under a 0700 directory (not a world-writable shared file)."""
    base = Path(tempfile.gettempdir()) / "cmdb-ssh"
    try:
        base.mkdir(mode=0o700, exist_ok=True)
        try:
            os.chmod(base, 0o700)
        except OSError:
            pass
        path = base / "known_hosts"
        if not path.exists():
            path.touch(mode=0o600)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        return str(path)
    except OSError:
        # Last resort: still avoid a shared world-writable path name.
        fallback = Path(tempfile.gettempdir()) / f"cmdb_known_hosts_{os.getuid()}"
        try:
            if not fallback.exists():
                fallback.touch(mode=0o600)
        except OSError:
            pass
        return str(fallback)


def _sanitize_probe_error(detail: str, max_len: int = _PROBE_ERROR_MAX) -> str:
    text = (detail or "").replace("\x00", "").strip()
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text or "ssh probe failed"


def _clamp_probe_timeout(timeout: int) -> int:
    try:
        value = int(timeout)
    except (TypeError, ValueError):
        value = _PROBE_TIMEOUT_DEFAULT
    return max(_PROBE_TIMEOUT_MIN, min(value, _PROBE_TIMEOUT_MAX))


def _has_control_chars(value: str) -> bool:
    return any(ord(c) < 32 or ord(c) == 127 for c in value)


def parse_ssh_target(ssh_field: str) -> tuple[list[str], str | None]:
    """Return (ssh argv prefix including destination, error)."""
    raw = (ssh_field or "").strip()
    if not raw:
        return [], "no ssh field"
    if _has_control_chars(raw):
        return [], "ssh target contains control characters"
    # Drop trailing parenthetical notes: "user@host (key name)"
    raw = re.sub(r"\s*\([^)]*\)\s*$", "", raw).strip()
    try:
        parts = shlex.split(raw)
    except ValueError as exc:
        return [], f"invalid ssh target quoting: {exc}"
    if not parts:
        return [], "empty ssh target"

    user_host = None
    port = None
    i = 0
    while i < len(parts):
        p = parts[i]
        if p in ("-p", "-P") and i + 1 < len(parts):
            port = parts[i + 1]
            i += 2
            continue
        if p.startswith("-p") and len(p) > 2 and p[2:].isdigit():
            port = p[2:]
            i += 1
            continue
        if user_host is None and not p.startswith("-"):
            user_host = p
            i += 1
            continue
        # Ignore unknown tokens rather than forwarding arbitrary SSH flags.
        i += 1

    if not user_host:
        return [], f"could not parse ssh target: {ssh_field}"
    if _has_control_chars(user_host):
        return [], f"unsafe or invalid ssh destination: {user_host}"
    if not _SSH_DEST_RE.match(user_host):
        return [], f"unsafe or invalid ssh destination: {user_host}"
    if "@" in user_host:
        user, _, host = user_host.rpartition("@")
        if not user or not host:
            return [], f"unsafe or invalid ssh destination: {user_host}"
    if port is not None:
        if not str(port).isdigit() or not (1 <= int(port) <= 65535):
            return [], f"invalid ssh port: {port}"

    argv = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"UserKnownHostsFile={_known_hosts_file()}",
        "-o",
        "LogLevel=ERROR",
        "-o",
        "GlobalKnownHostsFile=/dev/null",
    ]
    ssh_dir = os.environ.get("CMDB_SSH_DIR", "").strip()
    identity = os.environ.get("CMDB_SSH_IDENTITY", "").strip()
    identities: list[str] = []
    if ssh_dir and os.path.isdir(ssh_dir):
        for name in ("id_ed25519", "id_rsa", "id_ecdsa", "id_dsa"):
            path = os.path.join(ssh_dir, name)
            if os.path.isfile(path) and "\x00" not in path:
                identities.append(path)
    elif identity:
        if "\x00" in identity or not os.path.isfile(identity):
            return [], "invalid CMDB_SSH_IDENTITY path"
        identities.append(identity)
    if identities:
        argv.extend(["-o", "IdentitiesOnly=yes"])
        for path in identities:
            argv.extend(["-i", path])
    if port:
        argv.extend(["-p", str(port)])
    argv.append(user_host)
    return argv, None


def _probe_subprocess_env() -> dict[str, str]:
    """Minimal env for ssh — avoid leaking unrelated secrets into the child."""
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "USER", "LOGNAME", "TMPDIR")
    env = {k: v for k, v in os.environ.items() if k in keep and isinstance(v, str)}
    if "PATH" not in env:
        env["PATH"] = "/usr/bin:/bin"
    if "HOME" not in env:
        env["HOME"] = tempfile.gettempdir()
    env.setdefault("LANG", "C.UTF-8")
    return env


def _kb_to_bytes(value: str | None) -> int | None:
    if not value or not str(value).isdigit():
        return None
    return int(value) * 1024


def _fmt_bytes(n: int | None) -> str | None:
    if n is None:
        return None
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    x = float(n)
    for unit in units:
        if x < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(x)} {unit}"
            return f"{x:.1f} {unit}"
        x /= 1024
    return None


def _parse_lsblk_kv(line: str) -> dict[str, str]:
    """Parse `lsblk -P` KEY=\"value\" pairs."""
    out: dict[str, str] = {}
    for match in re.finditer(r'([A-Z0-9_-]+)="([^"]*)"', line):
        out[match.group(1)] = match.group(2).strip()
    return out


def _classify_drive(name: str, rota: str | None, tran: str | None, blk_type: str | None) -> str:
    n = (name or "").lower()
    t = (tran or "").lower()
    bt = (blk_type or "").lower()
    if n.startswith("nvme") or t == "nvme":
        return "NVMe"
    if t in ("sata", "ata", "sas", "scsi") or n.startswith(("sd", "hd")):
        if rota == "0":
            return "SSD"
        if rota == "1":
            return "HDD"
    if t == "usb" or n.startswith("sd") and rota == "0" and t == "usb":
        return "USB"
    if t == "usb":
        return "USB"
    if n.startswith("mmc") or t == "mmc":
        return "eMMC/SD"
    if n.startswith(("vd", "xvd")) or t in ("virtio", "xen"):
        return "Virtual"
    if bt == "rom":
        return "Optical"
    if rota == "0":
        return "SSD"
    if rota == "1":
        return "HDD"
    return "Unknown"


def _resolve_parent_disk(
    source: str,
    by_path: dict[str, dict[str, str]],
    by_name: dict[str, dict[str, str]],
) -> dict[str, str] | None:
    """Walk pkname chain from a mount source to the disk device."""
    path = source
    # Strip partition suffixes for mapper? keep as-is first
    node = by_path.get(path)
    if node is None:
        # Try basename match e.g. /dev/nvme0n1p2
        base = path.rsplit("/", 1)[-1]
        node = by_name.get(base)
    if node is None and path.startswith("/dev/mapper/"):
        # Leave LVM; try find slave via name
        node = by_name.get(path.rsplit("/", 1)[-1])
    seen: set[str] = set()
    while node:
        key = node.get("NAME") or node.get("PATH") or ""
        if key in seen:
            break
        seen.add(key)
        if (node.get("TYPE") or "").lower() == "disk":
            return node
        pk = node.get("PKNAME") or ""
        if not pk:
            # Treat leaf without pk as disk if not partition/lvm
            if (node.get("TYPE") or "").lower() in ("disk", ""):
                return node
            break
        node = by_name.get(pk)
    return node


def parse_probe_output(text: str) -> dict[str, Any]:
    kv: dict[str, str] = {}
    temps: list[dict[str, Any]] = []
    disks: list[dict[str, Any]] = []
    physical_disks: list[dict[str, Any]] = []
    nics: list[dict[str, Any]] = []
    blk_rows: list[dict[str, str]] = []
    sysblk: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("TEMP|"):
            bits = line.split("|", 2)
            if len(bits) < 3:
                continue
            _, name, val = bits
            try:
                celsius = float(val)
            except ValueError:
                continue
            temps.append({"name": name, "celsius": celsius})
            continue
        if line.startswith("DISK|"):
            bits = line.split("|")
            if len(bits) >= 7:
                try:
                    size = int(bits[3])
                    used = int(bits[4])
                    avail = int(bits[5])
                except ValueError:
                    continue
                pct_s = bits[6].rstrip("%")
                try:
                    pct = float(pct_s)
                except ValueError:
                    pct = None
                disks.append(
                    {
                        "source": bits[1],
                        "mount": bits[2],
                        "size_bytes": size,
                        "used_bytes": used,
                        "available_bytes": avail,
                        "use_percent": pct,
                        "size_human": _fmt_bytes(size),
                        "used_human": _fmt_bytes(used),
                        "available_human": _fmt_bytes(avail),
                    }
                )
            continue
        if line.startswith("PHYSDISK|"):
            row = _parse_lsblk_kv(line[len("PHYSDISK|") :])
            if row:
                size = int(row["SIZE"]) if (row.get("SIZE") or "").isdigit() else None
                rota = row.get("ROTA") or ""
                path = row.get("PATH") or (f"/dev/{row['NAME']}" if row.get("NAME") else None)
                physical_disks.append(
                    {
                        "device": path,
                        "name": row.get("NAME") or None,
                        "model": row.get("MODEL") or None,
                        "serial": row.get("SERIAL") or None,
                        "vendor": row.get("VENDOR") or None,
                        "capacity_bytes": size,
                        "capacity_human": _fmt_bytes(size),
                        "interface": (row.get("TRAN") or None),
                        "firmware": row.get("REV") or None,
                        "rotational": True if rota == "1" else False if rota == "0" else None,
                        "removable": True if row.get("RM") == "1" else False if row.get("RM") == "0" else None,
                    }
                )
            continue
        if line.startswith("NIC|"):
            bits = line.split("|", 2)
            if len(bits) >= 3:
                name = bits[1]
                fields: dict[str, str] = {}
                for part in bits[2].split("|"):
                    if "=" in part:
                        k, v = part.split("=", 1)
                        fields[k] = v
                speed_raw = (fields.get("speed_mbps") or "").strip()
                speed = None
                if speed_raw.isdigit():
                    speed = int(speed_raw)
                elif speed_raw and speed_raw not in ("-1", "Unknown!"):
                    digits = re.sub(r"\D", "", speed_raw)
                    speed = int(digits) if digits else None
                nics.append(
                    {
                        "name": name,
                        "mac": fields.get("mac") or None,
                        "mtu": int(fields["mtu"]) if (fields.get("mtu") or "").isdigit() else None,
                        "operstate": fields.get("operstate") or None,
                        "speed_mbps": speed,
                        "duplex": (fields.get("duplex") or None),
                        "ipv4": fields.get("ipv4") or None,
                    }
                )
            continue
        if line.startswith("BLK|"):
            row = _parse_lsblk_kv(line[4:])
            if row:
                blk_rows.append(row)
            continue
        if line.startswith("SYSBLK|"):
            bits = line.split("|", 2)
            if len(bits) >= 3:
                name = bits[1]
                fields = {}
                for part in bits[2].split("|"):
                    if "=" in part:
                        k, v = part.split("=", 1)
                        fields[k] = v
                sysblk[name] = fields
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            kv[k] = v

    by_path = {r["PATH"]: r for r in blk_rows if r.get("PATH")}
    by_name = {r["NAME"]: r for r in blk_rows if r.get("NAME")}

    for disk in disks:
        parent = _resolve_parent_disk(str(disk["source"]), by_path, by_name)
        name = (parent or {}).get("NAME") or ""
        # Enrich from sysfs if lsblk missing rota/model
        sys = sysblk.get(name, {})
        rota = (parent or {}).get("ROTA") or sys.get("rota") or ""
        tran = (parent or {}).get("TRAN") or ""
        model = (parent or {}).get("MODEL") or sys.get("model") or ""
        vendor = (parent or {}).get("VENDOR") or sys.get("vendor") or ""
        serial = (parent or {}).get("SERIAL") or ""
        blk_type = (parent or {}).get("TYPE") or ""
        drive_type = _classify_drive(name, rota, tran, blk_type)
        parent_size = None
        if parent and (parent.get("SIZE") or "").isdigit():
            parent_size = int(parent["SIZE"])
        disk["drive_type"] = drive_type
        disk["device"] = name or None
        disk["transport"] = tran or None
        disk["model"] = model or None
        disk["vendor"] = vendor or None
        disk["serial"] = serial or None
        disk["rotational"] = True if rota == "1" else False if rota == "0" else None
        disk["device_size_bytes"] = parent_size
        disk["device_size_human"] = _fmt_bytes(parent_size)
        disk["removable"] = True if (parent or {}).get("RM") == "1" else False if (parent or {}).get("RM") == "0" else None

    # Deduplicate physical disks by device path
    seen_pd: set[str] = set()
    uniq_physical: list[dict[str, Any]] = []
    for pd in physical_disks:
        key = str(pd.get("device") or pd.get("name") or "")
        if not key or key in seen_pd:
            continue
        seen_pd.add(key)
        # Enrich from sysfs
        sys = sysblk.get(str(pd.get("name") or ""), {})
        if not pd.get("model") and sys.get("model"):
            pd["model"] = sys["model"]
        if not pd.get("vendor") and sys.get("vendor"):
            pd["vendor"] = sys["vendor"]
        if pd.get("rotational") is None and sys.get("rota") in ("0", "1"):
            pd["rotational"] = sys["rota"] == "1"
        uniq_physical.append(pd)

    mem_total = _kb_to_bytes(kv.get("MEM_TOTAL_KB"))
    mem_avail = _kb_to_bytes(kv.get("MEM_AVAIL_KB"))
    mem_free = _kb_to_bytes(kv.get("MEM_FREE_KB"))
    mem_used = None
    if mem_total is not None and mem_avail is not None:
        mem_used = mem_total - mem_avail
    elif mem_total is not None and mem_free is not None:
        mem_used = mem_total - mem_free

    load = None
    if kv.get("LOADAVG"):
        try:
            parts = [float(x) for x in kv["LOADAVG"].split()]
            if len(parts) >= 3:
                load = {"1m": parts[0], "5m": parts[1], "15m": parts[2]}
        except ValueError:
            load = None

    cores = int(kv["CPU_CORES"]) if kv.get("CPU_CORES", "").isdigit() else None
    threads = int(kv["CPU_THREADS"]) if kv.get("CPU_THREADS", "").isdigit() else None
    uptime = int(kv["UPTIME_SEC"]) if kv.get("UPTIME_SEC", "").isdigit() else None

    # Dedupe temps by name keeping first
    seen = set()
    uniq_temps = []
    for t in temps:
        key = (t["name"], round(t["celsius"], 1))
        if key in seen:
            continue
        seen.add(key)
        uniq_temps.append(t)

    primary_temp = None
    if uniq_temps:
        # Prefer cpu-thermal / package
        for pref in ("cpu-thermal", "rpizero", "x86_pkg_temp", "Package id 0"):
            for t in uniq_temps:
                if pref.lower() in str(t["name"]).lower():
                    primary_temp = t["celsius"]
                    break
            if primary_temp is not None:
                break
        if primary_temp is None:
            primary_temp = uniq_temps[0]["celsius"]

    return {
        "os": {
            "name": kv.get("OS_NAME") or None,
            "version": kv.get("OS_VERSION") or None,
            "codename": kv.get("OS_CODENAME") or None,
            "arch": kv.get("ARCH") or None,
            "kernel": kv.get("KERNEL") or None,
        },
        "hostname": kv.get("HOSTNAME") or None,
        "cpu": {
            "model": kv.get("CPU_MODEL") or None,
            "cores": cores,
            "threads": threads,
        },
        "memory": {
            "total_bytes": mem_total,
            "used_bytes": mem_used,
            "available_bytes": mem_avail,
            "total_human": _fmt_bytes(mem_total),
            "used_human": _fmt_bytes(mem_used),
            "available_human": _fmt_bytes(mem_avail),
            "swap_total_bytes": _kb_to_bytes(kv.get("SWAP_TOTAL_KB")),
            "swap_free_bytes": _kb_to_bytes(kv.get("SWAP_FREE_KB")),
        },
        "loadavg": load,
        "uptime_seconds": uptime,
        "temperature_c": primary_temp,
        "temperatures": uniq_temps,
        "disks": disks,
        "physical_disks": uniq_physical,
        "nics": nics,
        "product": kv.get("PRODUCT") or None,
        "vendor": kv.get("VENDOR") or None,
        "board": kv.get("BOARD") or None,
    }


def probe_host(ssh_field: str, timeout: int = _PROBE_TIMEOUT_DEFAULT) -> dict[str, Any]:
    argv, err = parse_ssh_target(ssh_field)
    probed_at = datetime.now(timezone.utc).isoformat()
    if err:
        return {"ok": False, "probed_at": probed_at, "ssh": ssh_field, "error": err, "live": None}

    timeout = _clamp_probe_timeout(timeout)
    cmd = argv + ["bash", "-s"]
    try:
        completed = subprocess.run(
            cmd,
            input=REMOTE_SCRIPT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=_probe_subprocess_env(),
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "probed_at": probed_at,
            "ssh": ssh_field,
            "error": f"ssh timed out after {timeout}s",
            "live": None,
        }
    except FileNotFoundError:
        return {
            "ok": False,
            "probed_at": probed_at,
            "ssh": ssh_field,
            "error": "ssh client not available in this environment",
            "live": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "probed_at": probed_at,
            "ssh": ssh_field,
            "error": _sanitize_probe_error(str(exc)),
            "live": None,
        }

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        return {
            "ok": False,
            "probed_at": probed_at,
            "ssh": ssh_field,
            "error": _sanitize_probe_error(detail or f"ssh exited {completed.returncode}"),
            "live": None,
        }

    live = parse_probe_output(completed.stdout)
    return {"ok": True, "probed_at": probed_at, "ssh": ssh_field, "error": None, "live": live}


def static_hardware_from_live(live: dict[str, Any]) -> dict[str, Any]:
    """Map live probe into inventory-friendly hardware/os blocks."""
    hardware: dict[str, Any] = {}
    cpu = live.get("cpu") or {}
    if cpu.get("model") or cpu.get("cores"):
        hardware["cpu"] = {k: v for k, v in cpu.items() if v is not None}
    mem = live.get("memory") or {}
    if mem.get("total_bytes"):
        hardware["memory"] = {
            "total_bytes": mem["total_bytes"],
            "total_human": mem.get("total_human"),
        }
    disks = live.get("disks") or []
    physical = live.get("physical_disks") or []
    if physical:
        hardware["disks"] = [
            {
                "device": d.get("device"),
                "model": d.get("model"),
                "serial": d.get("serial"),
                "capacity_bytes": d.get("capacity_bytes"),
                "capacity_human": d.get("capacity_human"),
                "interface": d.get("interface"),
                "firmware": d.get("firmware"),
                "rotational": d.get("rotational"),
            }
            for d in physical
        ]
    if disks:
        hardware["filesystems"] = [
            {
                "device": d.get("source"),
                "mount": d.get("mount"),
                "size_bytes": d.get("size_bytes"),
                "size_human": d.get("size_human"),
                "disk_ref": f"/dev/{d['device']}" if d.get("device") else None,
            }
            for d in disks
        ]
        # Legacy key for older consumers
        hardware["storage"] = [
            {
                "source": d.get("source"),
                "mount": d.get("mount"),
                "size_bytes": d.get("size_bytes"),
                "size_human": d.get("size_human"),
                "drive_type": d.get("drive_type"),
                "device": d.get("device"),
                "model": d.get("model"),
                "transport": d.get("transport"),
            }
            for d in disks
        ]
    nics = live.get("nics") or []
    network = None
    if nics:
        network = {
            "interfaces": [
                {
                    "name": n.get("name"),
                    "mac": n.get("mac"),
                    "speed_mbps": n.get("speed_mbps"),
                    "duplex": n.get("duplex"),
                    "mtu": n.get("mtu"),
                    "ipv4": n.get("ipv4"),
                }
                for n in nics
            ]
        }
    if live.get("vendor"):
        hardware["vendor"] = live["vendor"]
    if live.get("product"):
        hardware["product"] = live["product"]
    if live.get("board"):
        hardware["platform"] = live["board"]
    os_block = {k: v for k, v in (live.get("os") or {}).items() if v}
    out: dict[str, Any] = {"hardware": hardware, "os": os_block}
    if network:
        out["network"] = network
    return out
