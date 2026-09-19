"""Tests for scan Add enrichment: MAC, source, dual-stack merge."""

from __future__ import annotations

from cmdb_api.scan import (
    build_server_document,
    merge_scan_add_entries,
    normalize_mac,
)


def test_normalize_mac_colon_and_cisco() -> None:
    assert normalize_mac("AA:BB:CC:DD:EE:FF") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("aa-bb-cc-dd-ee-ff") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("aabb.ccdd.eeff") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("not-a-mac") is None
    assert normalize_mac(None) is None


def test_build_server_document_persists_mac_and_source() -> None:
    doc = build_server_document(
        item_id="srv-lab-01",
        name="lab-01",
        ipv4="192.168.1.10",
        ipv6="fd00::10",
        env="lab",
        status="unknown",
        hostname="lab-01.example.com",
        ports=[22, 443],
        ssh_user="ops",
        notes=None,
        mac="AA:BB:CC:DD:EE:FF",
        source="ndp",
    )
    assert doc["addresses"]["ipv4"] == "192.168.1.10"
    assert doc["addresses"]["ipv6"] == "fd00::10"
    assert doc["addresses"]["mac"] == "aa:bb:cc:dd:ee:ff"
    assert doc["addresses"]["hostname"] == "lab-01"
    assert doc["addresses"]["dns"] == ["lab-01.example.com"]
    assert doc["ports_observed"] == [22, 443]
    assert doc["sources"] == ["network-scan", "scan:ndp"]
    assert doc["ssh"] == "ops@192.168.1.10"


def test_merge_scan_add_entries_dual_stack_by_hostname() -> None:
    merged = merge_scan_add_entries(
        [
            {
                "ip": "192.168.1.10",
                "ipv4": "192.168.1.10",
                "hostname": "k3s4.lan",
                "ports": [22],
                "mac": None,
                "source": "ipv4-sweep",
                "id": "srv-k3s4",
                "name": "k3s4",
            },
            {
                "ip": "fd00::10",
                "ipv6": "fd00::10",
                "hostname": "k3s4.lan",
                "ports": [22, 443],
                "mac": "02:00:00:00:00:0a",
                "source": "ndp",
                "id": "srv-k3s4",
                "name": "k3s4",
            },
            {
                "ip": "192.168.1.20",
                "ipv4": "192.168.1.20",
                "hostname": None,
                "ports": [80],
                "source": "arp",
            },
        ]
    )
    assert len(merged) == 2
    dual = next(e for e in merged if e.get("hostname") == "k3s4.lan")
    assert dual["ipv4"] == "192.168.1.10"
    assert dual["ipv6"] == "fd00::10"
    assert dual["ip"] == "192.168.1.10"
    assert dual["ports"] == [22, 443]
    assert dual["mac"] == "02:00:00:00:00:0a"
    assert dual["source"] == "ndp"
    assert dual["id"] == "srv-k3s4"
    solo = next(e for e in merged if e.get("ipv4") == "192.168.1.20")
    assert solo.get("ipv6") in (None, "")


def test_merge_leaves_same_family_separate() -> None:
    merged = merge_scan_add_entries(
        [
            {
                "ip": "192.168.1.10",
                "ipv4": "192.168.1.10",
                "hostname": "dup.lan",
                "ports": [22],
            },
            {
                "ip": "192.168.1.11",
                "ipv4": "192.168.1.11",
                "hostname": "dup.lan",
                "ports": [80],
            },
        ]
    )
    assert len(merged) == 2
