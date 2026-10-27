"""Domain errors shared by services, MCP servers and the API layer."""

from __future__ import annotations

from typing import Any


class ServiceError(Exception):
    """An expected business failure (not found, insufficient stock, ...).

    `code` is machine-readable, `message` is safe to show to users and to the
    model. `outcome` tells callers whether the operation definitely failed
    ("failed"), or whether its effect is unknown ("unknown").
    """

    http_status = 400

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None,
                 http_status: int | None = None, outcome: str = "failed") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.outcome = outcome
        if http_status is not None:
            self.http_status = http_status

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "outcome": self.outcome,
                "error": {"code": self.code, "message": self.message, "details": self.details}}


class NotFoundError(ServiceError):
    http_status = 404


class ConflictError(ServiceError):
    http_status = 409


class ValidationFailed(ServiceError):
    http_status = 422
