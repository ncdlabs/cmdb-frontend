"""Focused unit/API tests for CMDB hardening and parsers."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cmdb_api.addresses import AddressExtractor
from cmdb_api.probe import parse_ssh_target
from cmdb_api.scan import suggest_server_id, validate_server_id
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


class TestServerId:
    def test_suggest_and_validate(self) -> None:
        sid = suggest_server_id("192.168.1.50", "pi.local")
        assert sid == "srv-pi"
        assert validate_server_id(sid) is None
        assert validate_server_id("../etc/passwd") is not None


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


class TestApiTokenGuard:
    def test_open_when_unset(self) -> None:
        assert ApiTokenGuard("").verify(None) is True

    def test_requires_matching_token(self) -> None:
        guard = ApiTokenGuard("s3cret")
        assert guard.required is True
        assert guard.verify(None) is False
        assert guard.verify("wrong") is False
        assert guard.verify("s3cret") is True

    def test_bearer_extract(self) -> None:
        assert extract_bearer_or_header(None, "Bearer abc") == "abc"
        assert extract_bearer_or_header("hdr", "Bearer abc") == "hdr"


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

    def test_agent_search_unauthenticated(self, client: TestClient) -> None:
        res = client.get("/api/agent/search", params={"kind": "server"})
        assert res.status_code == 200
        assert res.json()["count"] >= 1
