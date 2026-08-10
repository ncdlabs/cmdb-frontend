"""Mutating-route authentication and static-path safety helpers."""

from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path

from fastapi import Header, HTTPException, Request


class ApiTokenGuard:
    """Shared-secret gate for probe / LAN scan / inventory write routes.

    When ``CMDB_API_TOKEN`` is unset or empty, checks are skipped (local dev).
    When set, callers must send ``X-CMDB-Token`` or ``Authorization: Bearer …``.
    """

    HEADER = "X-CMDB-Token"

    def __init__(self, token: str | None = None) -> None:
        if token is None:
            token = os.environ.get("CMDB_API_TOKEN", "")
        self._token = (token or "").strip()

    @property
    def required(self) -> bool:
        return bool(self._token)

    def verify(self, provided: str | None) -> bool:
        if not self._token:
            return True
        if not provided:
            return False
        # Hash both sides so compare_digest always compares equal-length digests
        # (avoids length-dependent short-circuit of the raw secret).
        expected = hashlib.sha256(self._token.encode("utf-8")).digest()
        got = hashlib.sha256(provided.strip().encode("utf-8")).digest()
        return secrets.compare_digest(expected, got)

    def status(self) -> dict[str, bool]:
        return {"token_required": self.required}


def active_guard() -> ApiTokenGuard:
    """Fresh guard from current env (tests / runtime token rotation)."""
    return ApiTokenGuard()


def extract_bearer_or_header(
    x_cmdb_token: str | None,
    authorization: str | None,
) -> str | None:
    if x_cmdb_token and x_cmdb_token.strip():
        return x_cmdb_token.strip()
    if authorization:
        scheme, _, rest = authorization.partition(" ")
        if scheme.lower() == "bearer" and rest.strip():
            return rest.strip()
    return None


async def require_api_token(
    request: Request,
    x_cmdb_token: str | None = Header(None, alias="X-CMDB-Token"),
    authorization: str | None = Header(None),
) -> None:
    """FastAPI dependency: enforce shared secret when configured."""
    _ = request
    provided = extract_bearer_or_header(x_cmdb_token, authorization)
    if not active_guard().verify(provided):
        raise HTTPException(
            status_code=401,
            detail="Valid CMDB API token required (X-CMDB-Token or Authorization: Bearer)",
            headers={"WWW-Authenticate": "Bearer"},
        )


class StaticPathResolver:
    """Resolve SPA fallback paths without leaving the static root."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def resolve_file(self, full_path: str) -> Path | None:
        if not full_path or "\x00" in full_path:
            return None
        # Disallow absolute and scheme-looking segments early.
        if full_path.startswith(("/", "\\")) or "://" in full_path:
            return None
        candidate = (self.root / full_path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return None
        if candidate.is_file():
            return candidate
        return None
