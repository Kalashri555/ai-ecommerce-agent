"""Shopping Cart tool implementations.

Identity: `user_id` always comes from the signed MCP request context, never
from tool arguments. Mutations: each one consumes a matching approval inside
the same DB transaction (see app.agent.approval_service.consume_approval).
"""

from __future__ import annotations

from typing import Any

from app.agent import approval_service
from app.agent import tool_schemas as ts
from app.auth.mcp_context import ToolContext
from app.services import cart_service


def get_cart(db, ctx: ToolContext, p: ts.EmptyInput) -> dict[str, Any]:
    return cart_service.get_cart(db, ctx.user_id)


def calculate_cart_total(db, ctx: ToolContext, p: ts.EmptyInput) -> dict[str, Any]:
    return cart_service.calculate_cart_total(db, ctx.user_id)


def checkout_preview(db, ctx: ToolContext, p: ts.EmptyInput) -> dict[str, Any]:
    return cart_service.checkout_preview(db, ctx.user_id)


def _consume(db, ctx: ToolContext, action: str, p) -> None:
    approval_service.consume_approval(db, approval_id=ctx.approval_id, user_id=ctx.user_id, action_type=action,
                                      arguments=p.model_dump(exclude_none=True))


def add_to_cart(db, ctx: ToolContext, p: ts.AddToCartInput) -> dict[str, Any]:
    _consume(db, ctx, "add_to_cart", p)
    return cart_service.add_to_cart(db, ctx.user_id, p.product_id, p.quantity)


def update_cart(db, ctx: ToolContext, p: ts.UpdateCartInput) -> dict[str, Any]:
    _consume(db, ctx, "update_cart", p)
    return cart_service.update_cart_item(db, ctx.user_id, p.product_id, p.quantity)


def remove_from_cart(db, ctx: ToolContext, p: ts.RemoveFromCartInput) -> dict[str, Any]:
    _consume(db, ctx, "remove_from_cart", p)
    return cart_service.remove_from_cart(db, ctx.user_id, p.product_id)


def place_order(db, ctx: ToolContext, p: ts.PlaceOrderInput) -> dict[str, Any]:
    _consume(db, ctx, "place_order", p)
    # The approval id doubles as the idempotency key: one approval -> at most one order.
    return cart_service.place_order(db, ctx.user_id, p.expected_cart_hash, idempotency_key=ctx.approval_id or "")
