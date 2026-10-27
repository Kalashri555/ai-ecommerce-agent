"""Tool registry/executor, MCP discovery & invocation, and the approval workflow."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from decimal import Decimal

from mcp import StdioServerParameters
from sqlalchemy import select

from app.agent import approval_service
from app.agent.mcp_client import MCPClientManager, MCPUnavailable
from app.agent.tool_executor import ExecContext
from app.agent.tool_registry import REGISTERED
from app.agent.tool_schemas import TOOL_INPUT_MODELS
from app.auth.mcp_context import META_KEY, build_meta
from app.auth.signing import sign
from app.database.connection import session_scope
from app.database.models import Order, PendingApproval, Product, utcnow
from app.services import cart_service
from app.tests.conftest import login, run

CATALOG_TOOLS = {"search_products", "get_product_details", "check_inventory", "get_product_reviews",
                 "compare_products", "rank_products"}
CART_TOOLS = {"get_cart", "calculate_cart_total", "checkout_preview", "add_to_cart", "update_cart",
              "remove_from_cart", "place_order"}


def ex(client, name, args, user_id=1, conv=None):
    executor = client.app.state.executor
    raw = args if isinstance(args, str) else json.dumps(args)
    return run(client, executor.execute, name, raw, ExecContext(user_id, conv, 1))


# ------------------------------------------------------------------ discovery
def test_mcp_discovers_tools_from_both_servers(client):
    status = client.app.state.mcp.status()
    assert status["product_catalog"]["connected"] and status["shopping_cart"]["connected"]
    assert set(status["product_catalog"]["tools"]) == CATALOG_TOOLS
    assert set(status["shopping_cart"]["tools"]) == CART_TOOLS


def test_discovered_tools_become_openai_function_tools(client):
    tools = client.app.state.registry.openai_tools()
    assert {t["name"] for t in tools} == CATALOG_TOOLS | CART_TOOLS
    for t in tools:
        assert t["type"] == "function" and t["description"] and t["parameters"]["type"] == "object"
    place = next(t for t in tools if t["name"] == "place_order")
    assert "expected_cart_hash" not in place["parameters"]["properties"]  # backend-filled
    assert "user_id" not in json.dumps(tools)  # identity never comes from the model


def test_mcp_schemas_match_pydantic_models(client):
    for tool in client.app.state.mcp.discovered_tools():
        model_fields = set(TOOL_INPUT_MODELS[tool.name].model_fields)
        assert set(tool.input_schema.get("properties", {})) == model_fields, tool.name
        assert tool.name in REGISTERED


# ------------------------------------------------------------------ execution
def test_valid_tool_call_routed_to_catalog_server(client):
    out = ex(client, "search_products", {"category": "laptop", "max_price": 80000, "limit": 3})
    assert out.trace["status"] == "ok" and out.trace["server"] == "product_catalog"
    assert out.output["returned"] == 3 and len(out.ui["products"]) == 3


def test_cart_read_routed_to_cart_server(client):
    out = ex(client, "get_cart", {})
    assert out.trace["server"] == "shopping_cart" and out.output["items"] == []


def test_unknown_tool_rejected(client):
    out = ex(client, "delete_database", {})
    assert out.trace["status"] == "rejected" and out.output["error"]["code"] == "unknown_tool"


def test_invalid_json_and_invalid_arguments_rejected(client):
    out = ex(client, "search_products", "{not json")
    assert out.output["error"]["code"] == "invalid_json"
    out = ex(client, "search_products", {"limit": 999})
    assert out.output["error"]["code"] == "invalid_arguments" and out.trace["status"] == "rejected"
    out = ex(client, "get_cart", {"user_id": 2})  # trying to pick another user
    assert out.output["error"]["code"] == "invalid_arguments"


def test_tool_error_is_structured(client):
    out = ex(client, "get_product_details", {"product_id": "LAP-999"})
    assert out.trace["status"] == "error"
    assert out.output["ok"] is False and out.output["error"]["code"] == "product_not_found"


def test_result_size_is_bounded(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "max_tool_result_chars", 500)
    out = ex(client, "search_products", {"limit": 20})
    assert len(json.dumps(out.output)) < 2000 and out.output.get("truncated")


# ------------------------------------------------------------------ approvals
def _pending_count():
    with session_scope() as db:
        return len(db.scalars(select(PendingApproval).where(PendingApproval.status == "pending")).all())


def test_mutation_requires_approval_and_does_not_execute(client):
    out = ex(client, "add_to_cart", {"product_id": "MOU-001", "quantity": 1})
    assert out.trace["status"] == "awaiting_approval" and out.output["status"] == "approval_required"
    assert out.approval["summary"]["product_name"] == "Logitech M235 Wireless Mouse"
    assert out.approval["summary"]["estimated_price"] == "899.00"
    with session_scope() as db:
        assert cart_service.get_cart(db, 1)["items"] == []


def test_out_of_stock_does_not_create_approval(client):
    before = _pending_count()
    out = ex(client, "add_to_cart", {"product_id": "LAP-010"})
    assert out.output["error"]["code"] == "out_of_stock" and _pending_count() == before


def test_approved_action_executes_exactly_once(client, auth):
    a = ex(client, "add_to_cart", {"product_id": "MOU-001", "quantity": 2}).approval
    r = client.post(f"/api/approvals/{a['approval_id']}/approve", json={"continue_agent": False}, headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"]["approval_status"] == "executed" and body["approval"]["status"] == "executed"
    assert body["result"]["quantity_in_cart"] == 2
    # Replay is refused and the cart is unchanged.
    r2 = client.post(f"/api/approvals/{a['approval_id']}/approve", json={"continue_agent": False}, headers=auth)
    assert r2.status_code == 409
    assert client.get("/api/cart", headers=auth).json()["item_count"] == 2


def test_cancelled_action_never_executes(client, auth):
    a = ex(client, "add_to_cart", {"product_id": "MOU-003"}).approval
    assert client.post(f"/api/approvals/{a['approval_id']}/cancel", headers=auth).json()["approval"]["status"] == "cancelled"
    assert client.post(f"/api/approvals/{a['approval_id']}/approve", headers=auth).status_code == 409
    assert client.get("/api/cart", headers=auth).json()["items"] == []


def test_approval_cannot_be_used_by_another_user(client):
    a = ex(client, "add_to_cart", {"product_id": "MOU-001"}, user_id=1).approval
    bob = login(client, "bob")
    assert client.post(f"/api/approvals/{a['approval_id']}/approve", headers=bob).status_code == 404
    assert client.get("/api/cart", headers=bob).json()["items"] == []


def test_expired_approval_rejected(client, auth):
    a = ex(client, "add_to_cart", {"product_id": "MOU-001"}).approval
    with session_scope() as db:
        db.get(PendingApproval, a["approval_id"]).expires_at = utcnow() - timedelta(minutes=1)
    r = client.post(f"/api/approvals/{a['approval_id']}/approve", headers=auth)
    assert r.status_code == 410
    assert client.get("/api/cart", headers=auth).json()["items"] == []


def test_price_change_invalidates_approval(client, auth):
    a = ex(client, "add_to_cart", {"product_id": "MOU-006"}).approval
    with session_scope() as db:
        db.scalar(select(Product).where(Product.sku == "MOU-006")).price = Decimal("1795.00")
    r = client.post(f"/api/approvals/{a['approval_id']}/approve", json={"continue_agent": False}, headers=auth).json()
    assert r["result"]["approval_status"] == "invalidated" and r["result"]["error"]["code"] == "price_changed"
    assert client.get("/api/cart", headers=auth).json()["items"] == []


def test_mcp_server_itself_refuses_unapproved_mutation(client):
    mcp = client.app.state.mcp
    res = run(client, mcp.call_tool, "add_to_cart", {"product_id": "MOU-001", "quantity": 1}, build_meta(1), idempotent=False)
    assert not res.ok and res.data["error"]["code"] == "approval_required"
    # An approval for a DIFFERENT payload cannot be reused.
    a = ex(client, "add_to_cart", {"product_id": "MOU-001", "quantity": 1}).approval
    with session_scope() as db:
        approval_service.approve(db, a["approval_id"], 1)
    res = run(client, mcp.call_tool, "add_to_cart", {"product_id": "MOU-001", "quantity": 5},
              build_meta(1, a["approval_id"]), idempotent=False)
    assert res.data["error"]["code"] == "approval_invalid"
    # Another user cannot use it either.
    res = run(client, mcp.call_tool, "add_to_cart", {"product_id": "MOU-001", "quantity": 1},
              build_meta(2, a["approval_id"]), idempotent=False)
    assert res.data["error"]["code"] == "approval_invalid"


def test_forged_user_context_rejected(client):
    mcp = client.app.state.mcp
    forged = {META_KEY: sign({"uid": 2, "typ": "mcp"}, "wrong-secret", 60)}
    res = run(client, mcp.call_tool, "get_cart", {}, forged, idempotent=True)
    assert not res.ok and res.data["error"]["code"] == "unauthenticated"
    res = run(client, mcp.call_tool, "get_cart", {}, {}, idempotent=True)
    assert res.data["error"]["code"] == "unauthenticated"


def test_checkout_requires_approval_and_is_idempotent(client, auth):
    client.post("/api/cart/items", json={"product_id": "KEY-003", "quantity": 2}, headers=auth)
    prev = client.post("/api/checkout/preview", headers=auth).json()["approval"]
    assert prev["summary"]["estimated_total"] == "1398.00" and prev["status"] == "pending"
    with session_scope() as db:
        assert db.scalars(select(Order)).all() == []   # nothing ordered by preview
    r = client.post("/api/checkout/confirm", json={"approval_id": prev["approval_id"]}, headers=auth).json()
    assert r["result"]["approval_status"] == "executed" and r["result"]["order_id"].startswith("DEMO-")
    again = client.post("/api/checkout/confirm", json={"approval_id": prev["approval_id"]}, headers=auth)
    assert again.status_code == 409
    assert len(client.get("/api/orders", headers=auth).json()["orders"]) == 1


def test_checkout_rejected_when_cart_changed_after_preview(client, auth):
    client.post("/api/cart/items", json={"product_id": "KEY-003", "quantity": 1}, headers=auth)
    prev = client.post("/api/checkout/preview", headers=auth).json()["approval"]
    client.patch("/api/cart/items/KEY-003", json={"quantity": 3}, headers=auth)
    r = client.post("/api/checkout/confirm", json={"approval_id": prev["approval_id"]}, headers=auth).json()
    assert r["result"]["approval_status"] == "invalidated" and r["result"]["error"]["code"] == "cart_changed"


# ------------------------------------------------------------------ MCP failure
def test_unavailable_mcp_server_gives_understandable_error():
    async def scenario():
        bad = StdioServerParameters(command="definitely-not-a-real-binary-xyz", args=[])
        mgr = MCPClientManager({"shopping_cart": bad})
        await mgr.start()
        status = mgr.status()["shopping_cart"]
        try:
            await mgr.call_tool("get_cart", {}, build_meta(1), idempotent=True)
            raised = None
        except MCPUnavailable as exc:
            raised = str(exc)
        await mgr.stop()
        return status, raised

    status, raised = asyncio.run(scenario())
    assert status["connected"] is False and "unavailable" in status["error"]
    assert raised  # a clear MCPUnavailable message, never a fake success
