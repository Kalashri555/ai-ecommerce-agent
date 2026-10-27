"""Integration: launch the REAL MCP servers as subprocesses over stdio.

This is the same transport the running application uses. No network needed.
"""

from __future__ import annotations

import asyncio

import pytest

from app.agent.mcp_client import MCPClientManager
from app.auth.mcp_context import build_meta

pytestmark = pytest.mark.integration


def test_stdio_servers_discovery_and_routing(fresh_db):
    async def scenario():
        mgr = MCPClientManager()  # default targets = stdio subprocesses
        await mgr.start()
        try:
            status = mgr.status()
            search = await mgr.call_tool("search_products", {"category": "wireless_mouse", "max_price": 2000},
                                         build_meta(1), idempotent=True)
            cart = await mgr.call_tool("get_cart", {}, build_meta(1), idempotent=True)
            unapproved = await mgr.call_tool("add_to_cart", {"product_id": "MOU-001"}, build_meta(1), idempotent=False)
        finally:
            await mgr.stop()
        return status, search, cart, unapproved

    status, search, cart, unapproved = asyncio.run(scenario())
    print("\nDiscovered via stdio:", {k: v["tools"] for k, v in status.items()})
    assert status["product_catalog"]["connected"] and status["shopping_cart"]["connected"]
    assert "search_products" in status["product_catalog"]["tools"]
    assert "add_to_cart" in status["shopping_cart"]["tools"]
    assert search.server == "product_catalog" and search.ok and search.data["total_matches"] > 0
    assert cart.server == "shopping_cart" and cart.ok
    assert not unapproved.ok and unapproved.data["error"]["code"] == "approval_required"
