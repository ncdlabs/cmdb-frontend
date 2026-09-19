"""Focused unit/API tests for CMDB hardening and parsers."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cmdb_api.addresses import AddressExtractor
from cmdb_api.probe import parse_ssh_target
from cmdb_api.scan import suggest_server_id, validate_server_id, validate_ssh_user
from cmdb_api.security import ApiTokenGuard, StaticPathResolver, extract_bearer_or_header


@pytest.fixture()
def inventory_root(tmp_path: Path) -> Path:
    root = tmp_path / "inventory"
    (root / "servers").mkdir(parents=True)
    (root / "services").mkdir()
    (root / "applications").mkdir()
    (root / "environments").mkdir()
    (root / "index.yaml").write_text("updated: '2026-08-09'\n", encoding="utf-8")
    (root / "servers" / "srv-test.yaml").write_text(
        "\n".join(
            [
                "id: srv-test",
                "kind: server",
                "name: test",
                "status: active",
                "addresses:",
                "  ipv4: 192.168.1.10",
                "ssh: ops@192.168.1.10",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return root


@pytest.fixture()
def client(inventory_root: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("CMDB_ROOT", str(inventory_root))
    monkeypatch.delenv("CMDB_API_TOKEN", raising=False)
    monkeypatch.delenv("CMDB_STATIC", raising=False)
    # Import after env so store root is correct.
    import cmdb_api.main as main_mod

    main_mod.STORE.root = inventory_root
    main_mod.STORE.reload()
    return TestClient(main_mod.app)


class TestAddressExtractor:
    def test_ssh_host_user_at_host(self) -> None:
        assert AddressExtractor.ssh_host("ops@192.168.1.6 -p 22") == "192.168.1.6"

    def test_ssh_host_ipv6_brackets(self) -> None:
        assert AddressExtractor.ssh_host("ops@[2001:db8::1]") == "2001:db8::1"

    def test_normalize_ip(self) -> None:
        assert AddressExtractor.from_cidr_or_addr("192.168.1.5/24") == "192.168.1.5"
        assert AddressExtractor.ipv6_from_cidr_or_addr("2001:db8::1/64") == "2001:db8::1"

    def test_rejects_garbage(self) -> None:
        assert AddressExtractor.from_cidr_or_addr("not-an-ip") is None
        assert AddressExtractor.ssh_host("") is None
        assert AddressExtractor.ssh_host(None) is None


class TestSshTarget:
    def test_valid_target(self) -> None:
        argv, err = parse_ssh_target("ops@host.example -p 2222")
        assert err is None
        assert argv[-1] == "ops@host.example"
        assert "-p" in argv and "2222" in argv

    def test_rejects_proxy_command_style(self) -> None:
        argv, err = parse_ssh_target("ops@host;rm")
        assert argv == []
        assert err is not None

    def test_ignores_extra_flags(self) -> None:
        argv, err = parse_ssh_target("user@host -o ProxyCommand=evil")
        assert err is None
        assert argv[-1] == "user@host"
        assert "ProxyCommand=evil" not in argv

    def test_rejects_out_of_range_port(self) -> None:
        argv, err = parse_ssh_target("ops@host -p 70000")
        assert argv == []
        assert err is not None
        assert "port" in (err or "").lower()

    def test_rejects_zero_port(self) -> None:
        argv, err = parse_ssh_target("ops@host -p 0")
        assert argv == []
        assert err is not None

    def test_rejects_control_chars(self) -> None:
        argv, err = parse_ssh_target("ops@host\n-oProxyCommand=evil")
        assert argv == []
        assert err is not None

    def test_known_hosts_is_private_path(self) -> None:
        argv, err = parse_ssh_target("ops@host.example")
        assert err is None
        joined = " ".join(argv)
        assert "UserKnownHostsFile=" in joined
        assert "/tmp/cmdb_known_hosts " not in joined + " "
        assert "cmdb-ssh" in joined or "cmdb_known_hosts_" in joined

    def test_sanitize_probe_error_truncates(self) -> None:
        from cmdb_api.probe import _sanitize_probe_error

        long = "x" * 1000
        out = _sanitize_probe_error(long)
        assert len(out) <= 400
        assert out.endswith("…")
        assert "\x00" not in _sanitize_probe_error("a\x00b\n\tc")


class TestServerId:
    def test_suggest_and_validate(self) -> None:
        sid = suggest_server_id("192.168.1.50", "pi.local")
        assert sid == "srv-pi"
        assert validate_server_id(sid) is None
        assert validate_server_id("../etc/passwd") is not None

    def test_ssh_user_validation(self) -> None:
        assert validate_ssh_user(None) is None
        assert validate_ssh_user("ops") is None
        assert validate_ssh_user("ops;rm") is not None
        assert validate_ssh_user("a" * 40) is not None
        assert validate_ssh_user("../x") is not None


class TestScanCaps:
    def test_skips_oversized_ipv4_prefix(self) -> None:
        from cmdb_api.scan import scan_subnets

        result = scan_subnets(["10.0.0.0/8"], set())
        assert result["ok"] is True
        assert result["targets"] == 0
        assert any("Skipping IPv4 sweep" in n for n in result.get("notes") or [])

    def test_derive_subnets_unions_node_ip_with_sample_lan(self, monkeypatch) -> None:
        from cmdb_api.scan import derive_subnets

        monkeypatch.setenv("CMDB_NODE_IP", "192.168.1.109")
        monkeypatch.delenv("CMDB_LAN_CIDR", raising=False)
        items = [
            {
                "kind": "environment",
                "network": {"lan_cidr": "10.0.0.0/24"},
            },
            {
                "kind": "server",
                "addresses": {"ipv4": "10.0.0.20"},
            },
        ]
        subnets = derive_subnets(items)
        assert "192.168.1.0/24" in subnets
        assert "10.0.0.0/24" in subnets

    def test_derive_subnets_skips_cluster_overlay_node_ip(self, monkeypatch) -> None:
        from cmdb_api.scan import derive_subnets, private_lan_slash24

        monkeypatch.setenv("CMDB_NODE_IP", "10.42.1.5")
        monkeypatch.delenv("CMDB_LAN_CIDR", raising=False)
        assert private_lan_slash24("10.42.1.5") is None
        assert derive_subnets([]) == []

    def test_derive_subnets_honors_cmdb_lan_cidr(self, monkeypatch) -> None:
        from cmdb_api.scan import derive_subnets

        monkeypatch.delenv("CMDB_NODE_IP", raising=False)
        monkeypatch.setenv("CMDB_LAN_CIDR", "192.168.1.0/24, 10.10.0.0/24")
        subnets = derive_subnets([])
        assert "192.168.1.0/24" in subnets
        assert "10.10.0.0/24" in subnets

    def test_allows_typical_slash24(self) -> None:
        from cmdb_api.scan import IPV4_SWEEP_MAX_HOSTS

        # /24 has 254 hosts — always under the production cap.
        assert IPV4_SWEEP_MAX_HOSTS >= 254


class TestLoaderHardening:
    def test_duplicate_ids_reported(self, inventory_root: Path) -> None:
        from cmdb_api.loader import CmdbStore

        (inventory_root / "services" / "svc-dup.yaml").write_text(
            "\n".join(
                [
                    "id: srv-test",
                    "kind: service",
                    "name: colliding",
                    "status: active",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        store = CmdbStore(root=inventory_root, refresh_seconds=999)
        meta = store.meta()
        assert meta["error_count"] >= 1
        assert any("Duplicate CI id" in e["error"] for e in meta["load_errors"])
        # First-loaded server wins
        item = store.get_item("srv-test")
        assert item is not None
        assert item.get("kind") == "server"

    def test_by_address_matches_ipv6(self, inventory_root: Path) -> None:
        from cmdb_api.loader import CmdbStore

        (inventory_root / "servers" / "srv-v6.yaml").write_text(
            "\n".join(
                [
                    "id: srv-v6",
                    "kind: server",
                    "name: v6",
                    "status: active",
                    "addresses:",
                    "  ipv6: 2001:db8::99",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        store = CmdbStore(root=inventory_root, refresh_seconds=999)
        hits = store.by_address("2001:db8::99")
        assert any(h.get("id") == "srv-v6" for h in hits)


class TestStaticPathResolver:
    def test_blocks_traversal(self, tmp_path: Path) -> None:
        static = tmp_path / "static"
        static.mkdir()
        (static / "index.html").write_text("ok", encoding="utf-8")
        (static / "asset.txt").write_text("a", encoding="utf-8")
        secret = tmp_path / "secret.txt"
        secret.write_text("nope", encoding="utf-8")
        resolver = StaticPathResolver(static)
        assert resolver.resolve_file("asset.txt") == (static / "asset.txt").resolve()
        assert resolver.resolve_file("../secret.txt") is None
        assert resolver.resolve_file("foo/../../secret.txt") is None
        assert resolver.resolve_file("/etc/passwd") is None
        assert resolver.resolve_file("file://etc/passwd") is None


class TestApiTokenGuard:
    def test_open_when_unset(self) -> None:
        assert ApiTokenGuard("").verify(None) is True

    def test_requires_matching_token(self) -> None:
        guard = ApiTokenGuard("s3cret")
        assert guard.required is True
        assert guard.verify(None) is False
        assert guard.verify("wrong") is False
        assert guard.verify("s3cret") is True
        assert guard.verify(" s3cret ") is True

    def test_length_mismatch_still_false(self) -> None:
        guard = ApiTokenGuard("short")
        assert guard.verify("a-much-longer-candidate") is False

    def test_bearer_extract(self) -> None:
        assert extract_bearer_or_header(None, "Bearer abc") == "abc"
        assert extract_bearer_or_header("hdr", "Bearer abc") == "hdr"
        assert extract_bearer_or_header(None, "Basic abc") is None


class TestApiRoutes:
    def test_health_and_auth_status(self, client: TestClient) -> None:
        assert client.get("/api/health").json() == {"ok": True}
        assert client.get("/api/auth/status").json() == {"token_required": False}

    def test_live_probe_requires_token_when_configured(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CMDB_API_TOKEN", "test-token")
        denied = client.post("/api/items/srv-test/live")
        assert denied.status_code == 401
        allowed = client.post(
            "/api/items/srv-test/live",
            headers={"X-CMDB-Token": "test-token"},
        )
        # SSH may fail in CI; auth must succeed (not 401).
        assert allowed.status_code != 401

    def test_live_probe_persist_writes_specs_on_success(
        self, client: TestClient, inventory_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        live = {
            "os": {"name": "Debian GNU/Linux", "version": "13", "arch": "amd64"},
            "cpu": {"model": "Test CPU", "cores": 2, "threads": 4},
            "memory": {"total_bytes": 8589934592, "total_human": "8.0 GiB"},
            "physical_disks": [
                {
                    "device": "/dev/sda",
                    "model": "TEST-SSD",
                    "serial": "SERIAL1",
                    "capacity_bytes": 100000000000,
                    "capacity_human": "93.1 GiB",
                    "interface": "sata",
                    "rotational": False,
                }
            ],
            "nics": [
                {
                    "name": "eth0",
                    "mac": "02:00:00:00:00:01",
                    "speed_mbps": 1000,
                    "duplex": "full",
                    "mtu": 1500,
                    "ipv4": ["10.0.0.20/24"],
                }
            ],
        }

        def fake_probe(_ssh: str, timeout: int = 30) -> dict:
            return {
                "ok": True,
                "probed_at": "2026-08-10T00:00:00+00:00",
                "ssh": _ssh,
                "error": None,
                "live": live,
            }

        import cmdb_api.main as main_mod

        monkeypatch.setattr(main_mod, "probe_host", fake_probe)
        res = client.post("/api/items/srv-test/live", params={"persist": "true"})
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is True
        assert body["persisted"] is True
        assert "hardware" in body["applied"]["fields"]
        yaml_text = (inventory_root / "servers" / "srv-test.yaml").read_text(encoding="utf-8")
        assert "Test CPU" in yaml_text
        assert "Debian GNU/Linux" in yaml_text
        assert "status: active" in yaml_text
        detail = client.get("/api/items/srv-test").json()
        assert detail["hardware"]["cpu"]["model"] == "Test CPU"
        assert detail["os"]["name"] == "Debian GNU/Linux"

    def test_live_probe_persist_skips_write_on_failure(
        self, client: TestClient, inventory_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_probe(_ssh: str, timeout: int = 30) -> dict:
            return {
                "ok": False,
                "probed_at": "2026-08-10T00:00:00+00:00",
                "ssh": _ssh,
                "error": "connection refused",
                "live": None,
            }

        import cmdb_api.main as main_mod

        monkeypatch.setattr(main_mod, "probe_host", fake_probe)
        before = (inventory_root / "servers" / "srv-test.yaml").read_text(encoding="utf-8")
        res = client.post("/api/items/srv-test/live", params={"persist": "true"})
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is False
        assert body["persisted"] is False
        after = (inventory_root / "servers" / "srv-test.yaml").read_text(encoding="utf-8")
        assert after == before

    def test_confirm_identity_promotes_unknown_server(
        self, client: TestClient, inventory_root: Path
    ) -> None:
        from cmdb_api.scan import LAN_RESCAN_NOTE

        path = inventory_root / "servers" / "srv-unconfirmed.yaml"
        path.write_text(
            "\n".join(
                [
                    "id: srv-unconfirmed",
                    "kind: server",
                    "name: unconfirmed",
                    "status: unknown",
                    f"notes: {LAN_RESCAN_NOTE}",
                    "addresses:",
                    "  ipv4: 192.168.1.99",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        import cmdb_api.main as main_mod

        main_mod.STORE.reload()
        res = client.post("/api/items/srv-unconfirmed/confirm")
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is True
        assert body["status"] == "active"
        assert body.get("notes") in (None, "")
        yaml_text = path.read_text(encoding="utf-8")
        assert "status: active" in yaml_text
        assert LAN_RESCAN_NOTE not in yaml_text
        detail = client.get("/api/items/srv-unconfirmed").json()
        assert detail["status"] == "active"

    def test_confirm_identity_rejects_already_active(
        self, client: TestClient
    ) -> None:
        res = client.post("/api/items/srv-test/confirm")
        assert res.status_code == 400

    def test_settings_api_get_put(self, client: TestClient, inventory_root: Path) -> None:
        got = client.get("/api/settings")
        assert got.status_code == 200
        body = got.json()
        assert body["ok"] is True
        assert "effective" in body
        assert "deployment" in body

        put = client.put(
            "/api/settings",
            json={
                "default_ssh_user": "lou",
                "lan_cidrs": "192.168.1.0/24",
                "discover_select_all": True,
                "confirm_sets_ssh": True,
                "confirm_probes_persist": False,
                "default_env": "demo-lab",
            },
        )
        assert put.status_code == 200, put.text
        assert put.json()["stored"]["default_ssh_user"] == "lou"
        assert put.json()["effective"]["default_ssh_user"] == "lou"
        assert (inventory_root / ".cmdb" / "settings.yaml").is_file()

    def test_settings_rejects_bad_cidr(self, client: TestClient) -> None:
        res = client.put("/api/settings", json={"lan_cidrs": ["not-a-cidr"]})
        assert res.status_code == 400

    def test_confirm_sets_ssh_from_settings(
        self, client: TestClient, inventory_root: Path
    ) -> None:
        from cmdb_api.scan import LAN_RESCAN_NOTE

        client.put(
            "/api/settings",
            json={
                "default_ssh_user": "lou",
                "confirm_sets_ssh": True,
                "confirm_probes_persist": False,
            },
        )
        path = inventory_root / "servers" / "srv-need-ssh.yaml"
        path.write_text(
            "\n".join(
                [
                    "id: srv-need-ssh",
                    "kind: server",
                    "name: need-ssh",
                    "status: unknown",
                    f"notes: {LAN_RESCAN_NOTE}",
                    "addresses:",
                    "  ipv4: 192.168.1.77",
                    "ports_observed:",
                    "  - 22",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        import cmdb_api.main as main_mod

        main_mod.STORE.reload()
        res = client.post("/api/items/srv-need-ssh/confirm")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["status"] == "active"
        assert body["ssh_set"] is True
        assert body["ssh"] == "lou@192.168.1.77"
        assert "ssh: lou@192.168.1.77" in path.read_text(encoding="utf-8")

    def test_bearer_auth_accepted(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CMDB_API_TOKEN", "bearer-secret")
        denied = client.post("/api/network/scan")
        assert denied.status_code == 401
        allowed = client.post(
            "/api/network/scan",
            headers={"Authorization": "Bearer bearer-secret"},
        )
        assert allowed.status_code != 401

    def test_agent_search_unauthenticated(self, client: TestClient) -> None:
        res = client.get("/api/agent/search", params={"kind": "server"})
        assert res.status_code == 200
        assert res.json()["count"] >= 1

    def test_network_add_rejects_oversized_batch(self, client: TestClient) -> None:
        devices = [
            {"ip": f"10.0.0.{i}", "id": f"srv-host-10-0-0-{i}", "name": f"h{i}"}
            for i in range(65)
        ]
        res = client.post("/api/network/devices", json={"devices": devices})
        assert res.status_code == 422

    def test_network_add_rejects_bad_ssh_user(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CMDB_DEFAULT_SSH_USER", raising=False)
        res = client.post(
            "/api/network/devices",
            json={
                "devices": [
                    {
                        "ip": "10.0.0.99",
                        "id": "srv-bad-user",
                        "name": "bad",
                        "ssh_user": "ops;rm -rf /",
                        "ports": [22],
                    }
                ]
            },
        )
        # 400 with structured errors, or 422 if pydantic catches first
        assert res.status_code in (400, 422)
        if res.status_code == 400:
            body = res.json()
            detail = body.get("detail") or body
            assert detail.get("error_count", 0) >= 1 or "ssh_user" in str(detail).lower()
