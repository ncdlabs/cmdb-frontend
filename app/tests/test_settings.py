"""Tests for PVC operational settings (unit)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cmdb_api.settings import (
    effective_settings,
    load_stored_settings,
    save_settings,
    validate_settings_patch,
)


def test_save_and_effective_merge(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CMDB_DEFAULT_SSH_USER", "ops")
    monkeypatch.setenv("CMDB_LAN_CIDR", "10.0.0.0/24")
    monkeypatch.delenv("CMDB_NODE_IP", raising=False)

    save_settings(
        tmp_path,
        {
            "default_ssh_user": "lou",
            "lan_cidrs": ["192.168.1.0/24"],
            "confirm_sets_ssh": True,
            "confirm_probes_persist": True,
            "discover_select_all": False,
            "default_env": "lab",
        },
    )
    stored = load_stored_settings(tmp_path)
    assert stored["default_ssh_user"] == "lou"
    assert stored["lan_cidrs"] == ["192.168.1.0/24"]
    assert stored["discover_select_all"] is False

    eff = effective_settings(tmp_path)
    assert eff["default_ssh_user"] == "lou"
    assert eff["sources"]["default_ssh_user"] == "stored"
    assert "192.168.1.0/24" in eff["lan_cidrs"]
    assert "10.0.0.0/24" in eff["lan_cidrs"]
    assert eff["sources"]["lan_cidrs"] == "stored+env"
    assert eff["default_env"] == "lab"
    assert eff["confirm_probes_persist"] is True


def test_effective_falls_back_to_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CMDB_DEFAULT_SSH_USER", "ops")
    monkeypatch.delenv("CMDB_LAN_CIDR", raising=False)
    eff = effective_settings(tmp_path)
    assert eff["default_ssh_user"] == "ops"
    assert eff["sources"]["default_ssh_user"] == "env"


def test_validate_rejects_bad_cidr() -> None:
    with pytest.raises(ValueError, match="invalid lan_cidr"):
        validate_settings_patch({"lan_cidrs": ["not-a-cidr"]})
