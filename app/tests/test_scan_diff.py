"""Unit tests for network scan snapshot diffs."""

from __future__ import annotations

from pathlib import Path

from cmdb_api.scan import (
    diff_observations,
    load_last_network_scan,
    observation_record,
    save_last_network_scan,
)


def _host(ip: str, **kwargs) -> dict:
    base = {
        "ip": ip,
        "family": "ipv4",
        "hostname": None,
        "ports": [],
        "ssh_open": False,
        "ping": True,
        "mac": None,
        "source": "ipv4-sweep",
        "in_inventory": False,
    }
    base.update(kwargs)
    return base


def test_diff_first_scan_has_no_baseline() -> None:
    diff = diff_observations(None, [_host("10.0.0.1")])
    assert diff["baseline"] is False
    assert diff["counts"] == {"added": 0, "removed": 0, "modified": 0}
    assert diff["note"]


def test_diff_added_removed_modified() -> None:
    previous = {
        "scanned_at": "2026-09-18T00:00:00+00:00",
        "hosts": [
            observation_record(_host("10.0.0.1", hostname="a", ports=[22])),
            observation_record(_host("10.0.0.2", hostname="b", ports=[80])),
            observation_record(_host("10.0.0.3", hostname="c")),
        ],
    }
    current = [
        _host("10.0.0.1", hostname="a", ports=[22, 443], ssh_open=True),
        _host("10.0.0.2", hostname="b-renamed", ports=[80]),
        _host("10.0.0.4", hostname="new"),
    ]
    diff = diff_observations(previous, current)
    assert diff["baseline"] is True
    assert [h["ip"] for h in diff["added"]] == ["10.0.0.4"]
    assert [h["ip"] for h in diff["removed"]] == ["10.0.0.3"]
    mods = {m["ip"]: m["changes"] for m in diff["modified"]}
    assert mods["10.0.0.1"] == ["ports", "ssh_open"]
    assert mods["10.0.0.2"] == ["hostname"]
    assert diff["counts"] == {"added": 1, "removed": 1, "modified": 2}


def test_save_and_load_last_network_scan(tmp_path: Path) -> None:
    hosts = [_host("192.168.1.10", hostname="k3s4", ports=[22], ssh_open=True)]
    rel = save_last_network_scan(
        tmp_path,
        scanned_at="2026-09-18T12:00:00+00:00",
        subnets=["192.168.1.0/24"],
        hosts=hosts,
    )
    assert rel == ".cmdb/last-network-scan.json"
    loaded = load_last_network_scan(tmp_path)
    assert loaded is not None
    assert loaded["scanned_at"] == "2026-09-18T12:00:00+00:00"
    assert loaded["hosts"][0]["hostname"] == "k3s4"
    assert loaded["hosts"][0]["ports"] == [22]
