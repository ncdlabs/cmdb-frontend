"""Agent-oriented read-only CMDB HTTP API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response

from cmdb_api.loader import CmdbStore

AGENT_TAG = "agent"


def build_agent_router(store: CmdbStore) -> APIRouter:
    """Bind agent routes to a CmdbStore instance."""

    router = APIRouter(prefix="/api/agent", tags=[AGENT_TAG])

    @router.get(
        "",
        summary="Agent API discovery",
        description=(
            "Capability index for LLM/tool agents. Prefer these endpoints over "
            "the browse UI routes when gathering inventory context."
        ),
        response_description="Discovery document with endpoint map and examples",
    )
    def agent_discovery() -> dict[str, Any]:
        return {
            "name": "cmdb-agent-api",
            "version": "1.0.0",
            "scope": "read-only",
            "auth": "none",
            "openapi": "/openapi.json",
            "docs": "/docs",
            "notes": [
                "Inventory contains no secrets.",
                "Live SSH probe and LAN scan/add are not part of the agent API.",
                "When CMDB_API_TOKEN is set, those mutating UI routes require X-CMDB-Token.",
            ],
            "endpoints": {
                "GET /api/agent": "This discovery document",
                "GET /api/agent/search": "Search CIs (q, kind, status, env, role, runtime)",
                "GET /api/agent/ci/{id}": "Full CI detail with resolved deps/dependents",
                "GET /api/agent/context/{id}": "One-shot context bundle (?format=md for markdown)",
                "GET /api/agent/by-address": "Resolve IPv4/hostname/Tailscale/SSH host → servers",
                "GET /api/agent/by-role": "List servers declaring a role",
                "GET /api/meta": "Inventory counts and load errors",
            },
            "examples": {
                "search_k3s_servers": "/api/agent/search?kind=server&role=k3s",
                "context_json": "/api/agent/context/app-demo",
                "context_markdown": "/api/agent/context/srv-app-01?format=md",
                "by_address": "/api/agent/by-address?q=10.0.0.20",
                "by_role": "/api/agent/by-role?role=dns",
            },
        }

    @router.get(
        "/search",
        summary="Search configuration items",
        description=(
            "Compact CI summaries. Supports browse filters plus role and runtime. "
            "Use before fetching full context for a known id."
        ),
    )
    def agent_search(
        q: str | None = Query(None, description="Substring match across searchable fields"),
        kind: str | None = Query(None, description="server | service | application | environment"),
        status: str | None = Query(None, description="active | deprecated | planned | unknown"),
        env: str | None = Query(None, description="Environment id, e.g. demo-lab"),
        role: str | None = Query(None, description="Exact role match (servers)"),
        runtime: str | None = Query(None, description="Application runtime, e.g. k3s or host"),
    ) -> dict[str, Any]:
        items = store.list_items(
            q=q, kind=kind, status=status, env=env, role=role, runtime=runtime
        )
        return {"items": items, "count": len(items)}

    @router.get(
        "/ci/{item_id}",
        summary="Get full CI detail",
        description="Full configuration item with depends_on_resolved, dependents, and placement (apps).",
    )
    def agent_get_ci(item_id: str) -> dict[str, Any]:
        item = store.get_item(item_id)
        if not item:
            raise HTTPException(status_code=404, detail=f"CI not found: {item_id}")
        return item

    @router.get(
        "/context/{item_id}",
        summary="One-shot context bundle",
        description=(
            "Returns the CI plus depends_on, dependents, placement (applications), "
            "and related server summaries in one response. "
            "Pass format=md for LLM-friendly markdown."
        ),
        responses={
            200: {
                "content": {
                    "application/json": {},
                    "text/markdown": {},
                }
            }
        },
    )
    def agent_context(
        item_id: str,
        format: str | None = Query(  # noqa: A002 — API query param name
            None,
            description="json (default) or md",
        ),
    ) -> Any:
        bundle = store.context_bundle(item_id)
        if not bundle:
            raise HTTPException(status_code=404, detail=f"CI not found: {item_id}")
        fmt = (format or "json").strip().lower()
        if fmt in ("md", "markdown", "text"):
            return Response(
                content=store.format_context_markdown(bundle),
                media_type="text/markdown; charset=utf-8",
            )
        return bundle

    @router.get(
        "/by-address",
        summary="Resolve address to servers",
        description="Match IPv4/IPv6, hostname, Tailscale IP, DNS alias, or SSH host against server CIs.",
    )
    def agent_by_address(
        q: str = Query(..., min_length=1, description="Address or hostname fragment"),
    ) -> dict[str, Any]:
        items = store.by_address(q)
        return {"query": q, "items": items, "count": len(items)}

    @router.get(
        "/by-role",
        summary="Servers by role",
        description="List servers that declare the given roles[] entry.",
    )
    def agent_by_role(
        role: str = Query(..., min_length=1, description="Role name, e.g. k3s or dns"),
    ) -> dict[str, Any]:
        items = store.by_role(role)
        return {"role": role, "items": items, "count": len(items)}

    return router
