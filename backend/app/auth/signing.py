"""Small HMAC-signed token helpers (no third-party JWT dependency).

Used for two things:
1. Login tokens the React app sends as `Authorization: Bearer <token>`.
2. The *user context* the FastAPI backend attaches to every MCP tool call
   (in the MCP request `_meta`). The MCP servers verify the signature, so a
   tool call cannot pick an arbitrary user id - not from the LLM, and not from
   any other process that does not know the secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any


class InvalidSignature(Exception):
    pass


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def sign(payload: dict[str, Any], secret: str, ttl_seconds: int) -> str:
    body = dict(payload)
    body["exp"] = int(time.time()) + int(ttl_seconds)
    raw = _b64(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64(hmac.new(secret.encode(), raw.encode(), hashlib.sha256).digest())
    return f"{raw}.{sig}"


def verify(token: str, secret: str) -> dict[str, Any]:
    try:
        raw, sig = token.split(".", 1)
    except (ValueError, AttributeError) as exc:
        raise InvalidSignature("malformed token") from exc
    expected = _b64(hmac.new(secret.encode(), raw.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        raise InvalidSignature("bad signature")
    try:
        body = json.loads(_unb64(raw))
    except (ValueError, json.JSONDecodeError) as exc:
        raise InvalidSignature("bad payload") from exc
    if int(body.get("exp", 0)) < int(time.time()):
        raise InvalidSignature("token expired")
    return body
