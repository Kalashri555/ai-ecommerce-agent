"""Signed user context passed from the backend to the MCP servers.

The backend puts `{"ecommerce/context": <signed token>}` into the MCP request
`_meta`. The token carries the authenticated user id and, for state-changing
operations, the id of the *approved* action. MCP servers call
`read_context(meta)` and refuse the call if the signature is missing/invalid.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.auth.signing import InvalidSignature, sign, verify
from app.config import get_settings

META_KEY = "ecommerce/context"
_TTL_SECONDS = 120


@dataclass(frozen=True)
class ToolContext:
    user_id: int
    approval_id: str | None = None


class ContextError(Exception):
    pass


def build_meta(user_id: int, approval_id: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"uid": int(user_id), "typ": "mcp"}
    if approval_id:
        payload["apr"] = approval_id
    return {META_KEY: sign(payload, get_settings().app_secret_key, _TTL_SECONDS)}


def read_context(meta: dict[str, Any] | None) -> ToolContext:
    token = (meta or {}).get(META_KEY)
    if not token:
        raise ContextError("Missing authenticated user context.")
    try:
        body = verify(str(token), get_settings().app_secret_key)
    except InvalidSignature as exc:
        raise ContextError(f"Invalid user context: {exc}") from exc
    if body.get("typ") != "mcp":
        raise ContextError("Invalid user context type.")
    return ToolContext(user_id=int(body["uid"]), approval_id=body.get("apr"))
