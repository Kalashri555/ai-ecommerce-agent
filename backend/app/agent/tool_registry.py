"""Central tool registry: the allow-list of tools the agent may use.

Tool *definitions* (descriptions + JSON schemas) are discovered at runtime from
the MCP servers. The registry adds what the protocol does not know about:
risk level, approval policy, authorization policy, idempotency, a validated
input model, and a user-friendly label for the UI.

A tool is offered to the model only if it is BOTH registered here AND
discovered from its expected MCP server. Anything else is rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from app.agent import tool_schemas as ts
from app.agent.mcp_client import MCPClientManager, to_openai_tool

Risk = Literal["read", "write", "checkout"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    server: str                  # MCP server that must provide the tool
    input_model: type[BaseModel]
    risk: Risk
    requires_approval: bool
    idempotent: bool             # safe to retry after a transient failure?
    authorization: Literal["authenticated_user"]
    label: str                   # shown in the Tool Execution panel


_SPECS = [
    ToolSpec("search_products", "product_catalog", ts.SearchProductsInput, "read", False, True, "authenticated_user", "Searching the product catalog"),
    ToolSpec("get_product_details", "product_catalog", ts.ProductIdInput, "read", False, True, "authenticated_user", "Retrieving product specifications"),
    ToolSpec("check_inventory", "product_catalog", ts.CheckInventoryInput, "read", False, True, "authenticated_user", "Checking inventory"),
    ToolSpec("get_product_reviews", "product_catalog", ts.GetReviewsInput, "read", False, True, "authenticated_user", "Reading sample reviews"),
    ToolSpec("compare_products", "product_catalog", ts.CompareProductsInput, "read", False, True, "authenticated_user", "Comparing products"),
    ToolSpec("rank_products", "product_catalog", ts.RankProductsInput, "read", False, True, "authenticated_user", "Ranking products with the scoring algorithm"),
    ToolSpec("get_cart", "shopping_cart", ts.EmptyInput, "read", False, True, "authenticated_user", "Loading your cart"),
    ToolSpec("calculate_cart_total", "shopping_cart", ts.EmptyInput, "read", False, True, "authenticated_user", "Calculating the cart total"),
    ToolSpec("checkout_preview", "shopping_cart", ts.EmptyInput, "read", False, True, "authenticated_user", "Preparing the order summary"),
    ToolSpec("add_to_cart", "shopping_cart", ts.AddToCartInput, "write", True, False, "authenticated_user", "Adding to cart"),
    ToolSpec("update_cart", "shopping_cart", ts.UpdateCartInput, "write", True, False, "authenticated_user", "Updating cart quantity"),
    ToolSpec("remove_from_cart", "shopping_cart", ts.RemoveFromCartInput, "write", True, False, "authenticated_user", "Removing from cart"),
    ToolSpec("place_order", "shopping_cart", ts.PlaceOrderInput, "checkout", True, False, "authenticated_user", "Placing a simulated order"),
]

REGISTERED: dict[str, ToolSpec] = {s.name: s for s in _SPECS}

# The model never needs to see these; the backend fills them in.
_HIDDEN_PARAMS = {"place_order": {"expected_cart_hash"}}


class ToolRegistry:
    def __init__(self, mcp: MCPClientManager) -> None:
        self.mcp = mcp

    def get(self, name: str) -> ToolSpec | None:
        return REGISTERED.get(name)

    def is_available(self, name: str) -> bool:
        spec = REGISTERED.get(name)
        return spec is not None and self.mcp.server_for(name) == spec.server

    def available_specs(self) -> list[ToolSpec]:
        return [s for s in _SPECS if self.is_available(s.name)]

    def openai_tools(self) -> list[dict[str, Any]]:
        tools = []
        for d in self.mcp.discovered_tools():
            if not self.is_available(d.name):
                continue  # discovered but not allow-listed -> never offered
            t = to_openai_tool(d)
            for hidden in _HIDDEN_PARAMS.get(d.name, ()):
                t["parameters"]["properties"].pop(hidden, None)
                if "required" in t["parameters"]:
                    t["parameters"]["required"] = [r for r in t["parameters"]["required"] if r != hidden]
            tools.append(t)
        return tools

    def describe(self) -> list[dict[str, Any]]:
        """For the learning UI / GET /api/tools."""
        defs = {t["name"]: t for t in self.openai_tools()}
        out = []
        for s in _SPECS:
            out.append({
                "name": s.name, "server": s.server, "risk": s.risk, "requires_approval": s.requires_approval,
                "available": self.is_available(s.name), "label": s.label,
                "openai_definition": defs.get(s.name),
            })
        return out
