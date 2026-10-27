"""Pydantic request/response models for the HTTP API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agent.tool_schemas import MAX_QTY_PER_ITEM, normalize_product_id


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str


class LoginResponse(BaseModel):
    token: str
    user: UserOut


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str | None = None
    message: str = Field(..., min_length=1, max_length=2000)


class ChatResponse(BaseModel):
    conversation_id: str
    reply: str
    products: list[dict[str, Any]] = []
    ranking: dict[str, Any] | None = None
    comparison: dict[str, Any] | None = None
    cart: dict[str, Any] | None = None
    pending_approvals: list[dict[str, Any]] = []
    tool_trace: list[dict[str, Any]] = []
    error: dict[str, Any] | None = None
    rounds: int = 0
    duration_ms: int = 0


class ApprovalDecisionRequest(BaseModel):
    continue_agent: bool = True


class ApprovalDecisionResponse(BaseModel):
    approval: dict[str, Any]
    result: dict[str, Any] | None = None
    agent: ChatResponse | None = None


class CartItemCreate(BaseModel):
    product_id: str
    quantity: int = Field(1, ge=1, le=MAX_QTY_PER_ITEM)

    @field_validator("product_id")
    @classmethod
    def _pid(cls, v: str) -> str:
        return normalize_product_id(v)


class CartItemUpdate(BaseModel):
    quantity: int = Field(..., ge=1, le=MAX_QTY_PER_ITEM)


class CheckoutConfirmRequest(BaseModel):
    approval_id: str = Field(..., min_length=10, max_length=64)
