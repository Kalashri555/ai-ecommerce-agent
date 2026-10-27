"""Shared plumbing for MCP tool functions.

* validates arguments with the shared Pydantic models,
* runs the business function inside a DB transaction,
* converts expected business failures into MCP tool errors (`isError=true`)
  whose text is a JSON object the agent can parse and reason about,
* never leaks stack traces or database internals to the client.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from mcp.server.mcpserver.exceptions import ToolError

from app.database.connection import session_scope
from app.services.errors import ServiceError

log = logging.getLogger("mcp.tools")


def _error(code: str, message: str, details: dict[str, Any] | None = None, outcome: str = "failed") -> ToolError:
    return ToolError(json.dumps({"ok": False, "outcome": outcome,
                                 "error": {"code": code, "message": message, "details": details or {}}}))


def run_tool(model: type[BaseModel], raw_args: dict[str, Any], fn: Callable[[Any, BaseModel], dict[str, Any]]) -> dict[str, Any]:
    try:
        params = model.model_validate({k: v for k, v in raw_args.items() if v is not None})
    except ValidationError as exc:
        issues = [{"field": ".".join(str(x) for x in e["loc"]), "message": e["msg"]} for e in exc.errors()]
        raise _error("invalid_arguments", "The tool arguments are invalid.", {"issues": issues})
    try:
        with session_scope() as db:
            return fn(db, params)
    except ToolError:
        raise
    except ServiceError as exc:
        raise ToolError(json.dumps(exc.to_dict()))
    except Exception:  # pragma: no cover - defensive
        log.exception("Unexpected error in MCP tool")
        # A DB error during a write: the transaction was rolled back, so the
        # operation did NOT happen. Report it as failed, not unknown.
        raise _error("internal_error", "The tool failed unexpectedly. Nothing was changed.")
