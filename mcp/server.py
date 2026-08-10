"""Thin MCP server wrapping the CMDB agent HTTP API."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

DEFAULT_BASE_URL = "https://cmdb.example.com"

mcp = FastMCP(
    "cmdb",
    instructions=(
        "Read-only CMDB tools for inventory browse. "
        "Prefer cmdb_context for one-shot CI + dependency graphs. "
        "Use cmdb_by_address to resolve IPs/hostnames. "
        "Live SSH probe is intentionally not exposed."
    ),
)


def _base_url() -> str:
    return os.environ.get("CMDB_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _get_json(path: str, params: dict[str, Any] | None = None) -> Any:
    url = f"{_base_url()}{path}"
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        resp = client.get(url, params={k: v for k, v in (params or {}).items() if v is not None})
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        if "text/markdown" in ctype or path.endswith(".md"):
            return resp.text
        if "application/json" in ctype or resp.text[:1] in "{[":
            return resp.json()
        return resp.text


def _dump(data: Any) -> str:
    if isinstance(data, str):
        return data
    return json.dumps(data, indent=2, default=str)


@mcp.tool()
def cmdb_meta() -> str:
    """Inventory metadata: totals, kind/status counts, load errors."""
    return _dump(_get_json("/api/meta"))


@mcp.tool()
def cmdb_search(
    q: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    env: str | None = None,
    role: str | None = None,
    runtime: str | None = None,
) -> str:
    """Search CMDB CIs. kind: server|service|application|environment. role/runtime optional."""
    return _dump(
        _get_json(
            "/api/agent/search",
            {
                "q": q,
                "kind": kind,
                "status": status,
                "env": env,
                "role": role,
                "runtime": runtime,
            },
        )
    )


@mcp.tool()
def cmdb_get(item_id: str) -> str:
    """Full CI detail including depends_on_resolved, dependents, and placement."""
    return _dump(_get_json(f"/api/agent/ci/{item_id}"))


@mcp.tool()
def cmdb_context(item_id: str, format: str = "json") -> str:  # noqa: A002
    """One-shot context bundle (CI + deps + dependents + related servers). format: json|md."""
    params = {"format": format} if format and format != "json" else None
    data = _get_json(f"/api/agent/context/{item_id}", params)
    return _dump(data)


@mcp.tool()
def cmdb_by_address(q: str) -> str:
    """Resolve IPv4, hostname, Tailscale IP, DNS alias, or SSH host to server CIs."""
    return _dump(_get_json("/api/agent/by-address", {"q": q}))


@mcp.tool()
def cmdb_by_role(role: str) -> str:
    """List servers that declare the given roles[] entry."""
    return _dump(_get_json("/api/agent/by-role", {"role": role}))


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
