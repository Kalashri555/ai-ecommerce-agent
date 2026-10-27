"""Shopping Cart MCP Server.

A real MCP server (official Python SDK, stdio transport) exposing cart and
simulated-checkout tools.

Security model
--------------
* Every call must carry a signed user context in the MCP request `_meta`
  (key "ecommerce/context"), created by the FastAPI backend from the logged-in
  user. Without a valid signature the call is refused.
* State-changing tools (add/update/remove/place_order) additionally require
  the id of an *approved* approval for exactly the same action and arguments.

Run standalone:  python -m mcp_servers.shopping_cart.server   (from the project root)
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Annotated, Any

from mcp_servers.common import bootstrap  # noqa: F401  (sys.path setup - must be first)

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from app.agent import tool_schemas as ts
from app.auth.mcp_context import ContextError, ToolContext, read_context
from app.database.connection import init_db
from mcp_servers.common.tool_runner import run_tool
from mcp_servers.shopping_cart import tools

logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

mcp = MCPServer(
    "shopping-cart",
    version="1.0.0",
    instructions="Cart and simulated checkout for the authenticated user. Mutations need an approved request.",
)


def _ctx(ctx: Context) -> ToolContext:
    try:
        return read_context(ctx.request_context.meta)
    except ContextError as exc:
        raise ToolError(json.dumps({"ok": False, "outcome": "failed",
                                    "error": {"code": "unauthenticated", "message": str(exc), "details": {}}}))


def _run(ctx: Context, model, args: dict[str, Any], fn) -> dict[str, Any]:
    tctx = _ctx(ctx)
    return run_tool(model, args, lambda db, p: fn(db, tctx, p))


@mcp.tool()
def get_cart(ctx: Context) -> dict[str, Any]:
    """Show the current user's cart: items, product IDs, quantities, unit prices, line totals and subtotal."""
    return _run(ctx, ts.EmptyInput, {}, tools.get_cart)


@mcp.tool()
def calculate_cart_total(ctx: Context) -> dict[str, Any]:
    """Recalculate the cart total from current prices: line totals, subtotal and estimated total.
    Shipping, taxes and discounts are not calculated in this demo (never invent them)."""
    return _run(ctx, ts.EmptyInput, {}, tools.calculate_cart_total)


@mcp.tool()
def checkout_preview(ctx: Context) -> dict[str, Any]:
    """Build the final order summary (items, current prices, totals, stock problems) before checkout."""
    return _run(ctx, ts.EmptyInput, {}, tools.checkout_preview)


@mcp.tool()
def add_to_cart(
    ctx: Context,
    product_id: Annotated[str, Field(description="Product ID such as 'MOU-001'.")],
    quantity: Annotated[int, Field(description="Units to add (1-10).", ge=1, le=10)] = 1,
) -> dict[str, Any]:
    """Add a product to the cart. Requires the user's explicit approval: the application will show the user
    a confirmation dialog and only execute after they approve. Validates product, stock and quantity limits."""
    return _run(ctx, ts.AddToCartInput, dict(product_id=product_id, quantity=quantity), tools.add_to_cart)


@mcp.tool()
def update_cart(
    ctx: Context,
    product_id: Annotated[str, Field(description="Product ID already in the cart.")],
    quantity: Annotated[int, Field(description="New quantity (1-10). Use remove_from_cart to remove.", ge=1, le=10)],
) -> dict[str, Any]:
    """Change the quantity of a cart item. Requires the user's explicit approval. Quantity 0 is rejected -
    use remove_from_cart instead."""
    return _run(ctx, ts.UpdateCartInput, dict(product_id=product_id, quantity=quantity), tools.update_cart)


@mcp.tool()
def remove_from_cart(
    ctx: Context,
    product_id: Annotated[str, Field(description="Product ID to remove from the cart.")],
) -> dict[str, Any]:
    """Remove a product from the cart. Requires the user's explicit approval. If several cart items could
    match what the user said, ask which one before calling this."""
    return _run(ctx, ts.RemoveFromCartInput, dict(product_id=product_id), tools.remove_from_cart)


@mcp.tool()
def place_order(
    ctx: Context,
    expected_cart_hash: Annotated[str | None, Field(description="Leave empty; the application fills it from the approved checkout summary.")] = None,
) -> dict[str, Any]:
    """Place a SIMULATED order for the whole cart (no real payment). Only call when the user explicitly asks to
    place/checkout the order. The application shows the final order summary and requires explicit approval;
    totals and stock are re-validated on the server and duplicate orders are prevented."""
    return _run(ctx, ts.PlaceOrderInput, dict(expected_cart_hash=expected_cart_hash), tools.place_order)


def main() -> None:
    init_db()
    mcp.run()


if __name__ == "__main__":
    main()
