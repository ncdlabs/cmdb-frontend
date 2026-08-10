"""FastAPI entrypoint for the CMDB browse UI and agent API."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from cmdb_api.agent_api import build_agent_router
from cmdb_api.loader import CmdbStore, default_cmdb_root
from cmdb_api.probe import probe_host
from cmdb_api.scan import scan_subnets
from cmdb_api.security import StaticPathResolver, active_guard, require_api_token

STORE = CmdbStore(root=default_cmdb_root())
_SCAN_LOCK = threading.Lock()

app = FastAPI(
    title="CMDB",
    version="1.3.0",
    description=(
        "Configuration management database. "
        "Browse UI uses `/api/items*`. "
        "LAN rescan (manual) writes new server YAML when inventory is writable. "
        "Optional live-probe persist writes hardware/os/network into server YAML. "
        "Mutating routes (live probe, network scan/add) require `CMDB_API_TOKEN` "
        "when that env var is set (`X-CMDB-Token` or Bearer). "
        "LLM/tool agents should prefer `/api/agent/*` "
        "(discovery at `GET /api/agent`, OpenAPI at `/openapi.json`)."
    ),
)


class NetworkDeviceAdd(BaseModel):
    ip: str = Field(..., min_length=1, max_length=128)
    id: str | None = Field(None, max_length=128)
    name: str | None = Field(None, max_length=128)
    hostname: str | None = Field(None, max_length=253)
    ipv4: str | None = Field(None, max_length=64)
    ipv6: str | None = Field(None, max_length=128)
    env: str | None = Field(None, max_length=64)
    status: str = Field("unknown", max_length=32)
    ports: list[int] = Field(default_factory=list, max_length=64)
    ssh_user: str | None = Field(None, max_length=32)
    notes: str | None = Field(None, max_length=2000)


class NetworkAddRequest(BaseModel):
    devices: list[NetworkDeviceAdd] = Field(..., min_length=1, max_length=64)


@app.middleware("http")
async def refresh_store(request, call_next):
    if request.url.path.startswith("/api/"):
        STORE.maybe_refresh()
    return await call_next(request)


@app.get("/api/health", tags=["browse"], summary="Health check")
def health() -> dict:
    return {"ok": True}


@app.get(
    "/api/auth/status",
    tags=["browse"],
    summary="Whether mutating routes require an API token",
)
def auth_status() -> dict[str, bool]:
    return active_guard().status()


@app.get("/api/meta", tags=["browse", "agent"], summary="Inventory metadata")
def meta() -> dict:
    return STORE.meta()


@app.get("/api/items", tags=["browse"], summary="List/filter CIs (browse UI)")
def list_items(
    q: str | None = Query(None),
    kind: str | None = Query(None),
    status: str | None = Query(None),
    env: str | None = Query(None),
) -> dict:
    items = STORE.list_items(q=q, kind=kind, status=status, env=env)
    return {"items": items, "count": len(items)}


@app.get("/api/items/{item_id}", tags=["browse"], summary="CI detail (browse UI)")
def get_item(item_id: str) -> dict:
    item = STORE.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"CI not found: {item_id}")
    return item


@app.post(
    "/api/items/{item_id}/live",
    tags=["browse"],
    summary="One-shot SSH live probe",
    description=(
        "Never call on an interval. Not part of the agent API / MCP tools. "
        "UI Refresh omits persist. With persist=true, successful probes write "
        "static hardware/os/network into inventory YAML (not live gauges; status unchanged)."
    ),
    dependencies=[Depends(require_api_token)],
)
def probe_item_live(
    item_id: str,
    persist: bool = Query(
        False,
        description="Write hardware/os/network from a successful probe into inventory YAML",
    ),
) -> dict:
    """One-shot SSH probe. Never polled automatically — UI Refresh only (persist off)."""
    item = STORE.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"CI not found: {item_id}")
    if item.get("kind") != "server":
        raise HTTPException(status_code=400, detail="Live probe is only supported for servers")
    ssh = item.get("ssh")
    if not ssh or not isinstance(ssh, str):
        raise HTTPException(
            status_code=400,
            detail="Server has no ssh field — add ssh: user@host to the inventory CI",
        )
    result = probe_host(ssh)
    if not persist:
        return result
    if not result.get("ok") or not isinstance(result.get("live"), dict):
        return {**result, "persisted": False}
    try:
        applied = STORE.apply_observed_specs(item_id, result["live"])
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"CI not found: {item_id}") from exc
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write inventory: {exc}") from exc
    return {**result, "persisted": True, "applied": applied}


@app.post(
    "/api/network/scan",
    tags=["browse"],
    summary="One-shot LAN rescan for unknown hosts",
    description=(
        "Sweeps inventory-derived subnets (env.network.lan_cidr or /24s from server "
        "IPv4s). Manual UI action only — never poll. Not part of the agent/MCP API."
    ),
    dependencies=[Depends(require_api_token)],
)
def network_scan() -> dict[str, Any]:
    if not _SCAN_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="A network scan is already in progress")
    try:
        ctx = STORE.network_scan_context()
        # IPv4 sweeps need subnets; IPv6 can still use NDP + AAAA with empty v4 lists.
        known = set(ctx["known_ips"])
        result = scan_subnets(ctx["subnets"], known, hostnames=ctx.get("hostnames") or [])
        unidentified = set(ctx["unidentified_ips"])
        for row in result.get("discovered") or []:
            ip = row.get("ip")
            row["in_unidentified"] = ip in unidentified
        result["writable"] = ctx["writable"]
        result["root"] = ctx["root"]
        return result
    finally:
        _SCAN_LOCK.release()


@app.post(
    "/api/network/devices",
    tags=["browse"],
    summary="Add selected LAN-discovered servers to inventory",
    description="Writes servers/<id>.yaml and reloads the store. Requires writable CMDB_ROOT.",
    dependencies=[Depends(require_api_token)],
)
def network_add_devices(body: NetworkAddRequest) -> dict[str, Any]:
    try:
        result = STORE.create_servers_from_scan([d.model_dump() for d in body.devices])
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result["created_count"] == 0 and result["error_count"] > 0:
        raise HTTPException(status_code=400, detail=result)
    return result


app.include_router(build_agent_router(STORE))


def _static_dir() -> Path | None:
    candidates = [
        Path(os.environ.get("CMDB_STATIC", "")),
        Path(__file__).resolve().parent.parent / "frontend" / "dist",
        Path("/app/static"),
    ]
    for path in candidates:
        if path and path.is_dir() and (path / "index.html").is_file():
            return path
    return None


_static = _static_dir()
_static_resolver: StaticPathResolver | None = None
if _static is not None:
    _static_resolver = StaticPathResolver(_static)
    assets = _static / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/style-guide")
    def style_guide_page() -> FileResponse:
        return FileResponse(_static / "index.html")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        assert _static_resolver is not None
        candidate = _static_resolver.resolve_file(full_path)
        if candidate is not None:
            return FileResponse(candidate)
        return FileResponse(_static / "index.html")
