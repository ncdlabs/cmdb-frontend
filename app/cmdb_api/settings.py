"""PVC-backed operational settings for CMDB (overrides empty Helm/env defaults)."""

from __future__ import annotations

import ipaddress
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

import yaml

SETTINGS_REL = Path(".cmdb") / "settings.yaml"

_SSH_USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,31}$")

# Keys persisted in .cmdb/settings.yaml
_WRITABLE_KEYS = (
    "default_ssh_user",
    "lan_cidrs",
    "default_env",
    "discover_select_all",
    "confirm_sets_ssh",
    "confirm_probes_persist",
)


def settings_path(root: Path) -> Path:
    return root / SETTINGS_REL


def _parse_cidrs(raw: Any) -> list[str]:
    """Normalize CIDR list from string or list; drop invalids."""
    parts: list[str] = []
    if isinstance(raw, str):
        for part in raw.replace(";", ",").split(","):
            if part.strip():
                parts.append(part.strip())
    elif isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, str) and entry.strip():
                parts.append(entry.strip())
    out: list[str] = []
    seen: set[str] = set()
    for cidr in parts:
        try:
            normalized = str(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            continue
        if normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
    return out


def _empty_stored() -> dict[str, Any]:
    return {
        "default_ssh_user": "",
        "lan_cidrs": [],
        "default_env": "",
        "discover_select_all": True,
        "confirm_sets_ssh": True,
        "confirm_probes_persist": False,
    }


def load_stored_settings(root: Path) -> dict[str, Any]:
    """Raw PVC settings (missing file → defaults). Does not merge env."""
    path = settings_path(root)
    base = _empty_stored()
    if not path.is_file():
        return base
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return base
    if not isinstance(raw, dict):
        return base
    out = dict(base)
    if isinstance(raw.get("default_ssh_user"), str):
        out["default_ssh_user"] = raw["default_ssh_user"].strip()
    out["lan_cidrs"] = _parse_cidrs(raw.get("lan_cidrs"))
    if isinstance(raw.get("default_env"), str):
        out["default_env"] = raw["default_env"].strip()
    for key in ("discover_select_all", "confirm_sets_ssh", "confirm_probes_persist"):
        if isinstance(raw.get(key), bool):
            out[key] = raw[key]
    if isinstance(raw.get("updated"), str):
        out["updated"] = raw["updated"]
    return out


def validate_settings_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a PUT body. Raises ValueError on bad input."""
    if not isinstance(patch, dict):
        raise ValueError("settings body must be an object")
    out: dict[str, Any] = {}

    if "default_ssh_user" in patch:
        user = patch.get("default_ssh_user")
        if user is None or user == "":
            out["default_ssh_user"] = ""
        elif not isinstance(user, str):
            raise ValueError("default_ssh_user must be a string")
        else:
            user = user.strip()
            if user and not _SSH_USER_RE.match(user):
                raise ValueError(
                    "default_ssh_user must be a plain POSIX username "
                    "(letters, digits, _-; max 32)"
                )
            out["default_ssh_user"] = user

    if "lan_cidrs" in patch:
        raw = patch.get("lan_cidrs")
        if raw is None or raw == "":
            out["lan_cidrs"] = []
        elif isinstance(raw, str):
            # Allow free-text; reject if any token is invalid
            tokens = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
            for tok in tokens:
                try:
                    ipaddress.ip_network(tok, strict=False)
                except ValueError as exc:
                    raise ValueError(f"invalid lan_cidr: {tok}") from exc
            out["lan_cidrs"] = _parse_cidrs(raw)
        elif isinstance(raw, list):
            for entry in raw:
                if not isinstance(entry, str):
                    raise ValueError("lan_cidrs entries must be strings")
                try:
                    ipaddress.ip_network(entry.strip(), strict=False)
                except ValueError as exc:
                    raise ValueError(f"invalid lan_cidr: {entry}") from exc
            out["lan_cidrs"] = _parse_cidrs(raw)
        else:
            raise ValueError("lan_cidrs must be a string or list of CIDRs")

    if "default_env" in patch:
        env = patch.get("default_env")
        if env is None or env == "":
            out["default_env"] = ""
        elif not isinstance(env, str):
            raise ValueError("default_env must be a string")
        else:
            env = env.strip()
            if len(env) > 64:
                raise ValueError("default_env too long (max 64)")
            out["default_env"] = env

    for key in ("discover_select_all", "confirm_sets_ssh", "confirm_probes_persist"):
        if key in patch:
            val = patch.get(key)
            if not isinstance(val, bool):
                raise ValueError(f"{key} must be a boolean")
            out[key] = val

    unknown = set(patch.keys()) - set(_WRITABLE_KEYS) - {"updated"}
    if unknown:
        raise ValueError(f"unknown settings keys: {', '.join(sorted(unknown))}")

    return out


def save_settings(root: Path, patch: dict[str, Any]) -> dict[str, Any]:
    """Merge patch into stored settings and write PVC file. Returns stored doc."""
    normalized = validate_settings_patch(patch)
    current = load_stored_settings(root)
    current.update(normalized)
    current["updated"] = date.today().isoformat()
    path = settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Persist only known keys + updated
    doc = {key: current[key] for key in _WRITABLE_KEYS}
    doc["updated"] = current["updated"]
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(
        yaml.safe_dump(doc, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    tmp.replace(path)
    return doc


def effective_settings(root: Path) -> dict[str, Any]:
    """Merge PVC settings over env defaults. Non-empty PVC wins for strings/lists."""
    stored = load_stored_settings(root)
    env_ssh = os.environ.get("CMDB_DEFAULT_SSH_USER", "").strip()
    env_cidrs = _parse_cidrs(os.environ.get("CMDB_LAN_CIDR", ""))

    ssh_source = "stored" if stored["default_ssh_user"] else ("env" if env_ssh else "default")
    default_ssh_user = stored["default_ssh_user"] or env_ssh

    # Union stored + env CIDRs (both apply); sources tracked separately
    cidr_set: list[str] = []
    seen: set[str] = set()
    for cidr in list(stored["lan_cidrs"]) + env_cidrs:
        if cidr not in seen:
            seen.add(cidr)
            cidr_set.append(cidr)
    if stored["lan_cidrs"] and env_cidrs:
        cidr_source = "stored+env"
    elif stored["lan_cidrs"]:
        cidr_source = "stored"
    elif env_cidrs:
        cidr_source = "env"
    else:
        cidr_source = "default"

    return {
        "default_ssh_user": default_ssh_user,
        "lan_cidrs": cidr_set,
        "default_env": stored["default_env"],
        "discover_select_all": bool(stored["discover_select_all"]),
        "confirm_sets_ssh": bool(stored["confirm_sets_ssh"]),
        "confirm_probes_persist": bool(stored["confirm_probes_persist"]),
        "updated": stored.get("updated"),
        "sources": {
            "default_ssh_user": ssh_source,
            "lan_cidrs": cidr_source,
            "default_env": "stored" if stored["default_env"] else "default",
            "discover_select_all": "stored",
            "confirm_sets_ssh": "stored",
            "confirm_probes_persist": "stored",
        },
        "stored": {
            "default_ssh_user": stored["default_ssh_user"],
            "lan_cidrs": list(stored["lan_cidrs"]),
            "default_env": stored["default_env"],
            "discover_select_all": bool(stored["discover_select_all"]),
            "confirm_sets_ssh": bool(stored["confirm_sets_ssh"]),
            "confirm_probes_persist": bool(stored["confirm_probes_persist"]),
            "updated": stored.get("updated"),
        },
    }


def deployment_facts() -> dict[str, Any]:
    """Read-only deploy facts (not writable via settings API)."""
    return {
        "cmdb_root": os.environ.get("CMDB_ROOT") or None,
        "cmdb_node_ip": (os.environ.get("CMDB_NODE_IP") or "").strip() or None,
        "cmdb_lan_cidr_env": (os.environ.get("CMDB_LAN_CIDR") or "").strip() or None,
        "cmdb_default_ssh_user_env": (os.environ.get("CMDB_DEFAULT_SSH_USER") or "").strip() or None,
        "cmdb_ssh_dir_configured": bool((os.environ.get("CMDB_SSH_DIR") or "").strip()),
        "cmdb_ssh_identity_configured": bool((os.environ.get("CMDB_SSH_IDENTITY") or "").strip()),
        "api_token_required": bool((os.environ.get("CMDB_API_TOKEN") or "").strip()),
    }
