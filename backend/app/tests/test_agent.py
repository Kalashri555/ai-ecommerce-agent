"""Agent loop tests with a scripted model (no API key needed).

The ScriptedLLM plays the role of the model: it emits the tool calls a model
would choose. Some steps look at the previous tool results before deciding
(e.g. "pick the cheapest qualifying mouse"), which mirrors tool-result-driven
behaviour. What is under test is the APPLICATION: tool validation, MCP
routing, approval gating, result feedback, memory, limits and error handling.
`test_live_openai.py` checks the real model's tool choices (optional).
"""

from __future__ import annotations

import json
from decimal import Decimal

from app.agent.openai_client import LLMError
from app.tests.conftest import call, last_outputs, login, text, tool_calls


def chat(client, auth, message, conversation_id=None):
    r = client.post("/api/chat", json={"message": message, "conversation_id": conversation_id}, headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


def tools_used(resp):
    return [t["tool"] for t in resp["tool_trace"]]


# 1 ------------------------------------------------------------------------
def test_find_wireless_mice_under_2000(client, auth, llm):
    llm.script(tool_calls(call("search_products", {"category": "wireless_mouse", "max_price": 2000})),
               text("Here are wireless mice under ₹2,000 from the demo catalog."))
    r = chat(client, auth, "Find wireless mice under ₹2,000.")
    assert tools_used(r) == ["search_products"]          # a single call is enough
    assert r["products"] and all(Decimal(p["price"]) <= 2000 for p in r["products"])
    fed_back = last_outputs(llm.inputs_seen[1])          # tool result went back to the model
    assert fed_back[0]["total_matches"] == len(r["products"])
    assert r["tool_trace"][0]["server"] == "product_catalog"


# 2 ------------------------------------------------------------------------
def test_compare_three_cheapest_laptops(client, auth, llm):
    def compare_from_search(inputs):
        ids = [p["product_id"] for p in last_outputs(inputs)[0]["products"]]
        return tool_calls(call("compare_products", {"product_ids": ids}))(inputs)

    llm.script(tool_calls(call("search_products", {"category": "laptop", "sort_by": "price_asc", "limit": 3})),
               compare_from_search, text("Comparison of the three cheapest laptops."))
    r = chat(client, auth, "Compare the three cheapest laptops.")
    assert tools_used(r) == ["search_products", "compare_products"]   # sequential, result-driven
    assert [p["product_id"] for p in r["comparison"]["products"]] == ["LAP-014", "LAP-016", "LAP-008"]


# 3 ------------------------------------------------------------------------
def test_laptops_16gb_under_80000(client, auth, llm):
    llm.script(tool_calls(call("search_products", {"category": "laptop", "max_price": 80000, "min_ram_gb": 16})),
               text("Laptops with 16 GB RAM under ₹80,000."))
    r = chat(client, auth, "Find laptops with 16 GB RAM under ₹80,000.")
    assert r["products"] and all(p["key_specs"]["ram_gb"] >= 16 and Decimal(p["price"]) <= 80000 for p in r["products"])


# 4 ------------------------------------------------------------------------
def test_rating_above_4_2(client, auth, llm):
    llm.script(tool_calls(call("search_products", {"min_rating": 4.2, "limit": 20})), text("Products rated above 4.2."))
    r = chat(client, auth, "Find a product with a rating above 4.2.")
    assert all(p["average_rating"] >= 4.2 for p in r["products"])


# 5 ------------------------------------------------------------------------
def test_add_to_cart_waits_for_approval_then_continues(client, auth, llm):
    llm.script(tool_calls(call("add_to_cart", {"product_id": "LAP-001", "quantity": 1})),
               text("Please confirm adding LAP-001 in the dialog."))
    r = chat(client, auth, "Add LAP-001 to my cart.")
    assert len(r["pending_approvals"]) == 1 and r["tool_trace"][0]["status"] == "awaiting_approval"
    assert client.get("/api/cart", headers=auth).json()["items"] == []        # nothing changed yet

    # The user approves -> backend executes via MCP -> agent continues (shows cart).
    llm.script(tool_calls(call("get_cart", {})), text("Added! Your cart now has the Lenovo IdeaPad Slim 5."))
    ap = r["pending_approvals"][0]["approval_id"]
    a = client.post(f"/api/approvals/{ap}/approve", json={"continue_agent": True}, headers=auth).json()
    assert a["result"]["approval_status"] == "executed"
    assert a["agent"]["cart"]["items"][0]["product_id"] == "LAP-001"
    event = [i for i in llm.inputs_seen[0] if i.get("role") == "developer" and "APPROVED" in i.get("content", "")]
    assert event, "the approval outcome is given to the model as an application event"


# 6 ------------------------------------------------------------------------
def test_show_cart(client, auth, llm):
    client.post("/api/cart/items", json={"product_id": "MOU-001", "quantity": 1}, headers=auth)
    llm.script(tool_calls(call("get_cart", {})), text("Your cart has one mouse."))
    r = chat(client, auth, "Show me my cart.")
    assert r["cart"]["items"][0]["product_id"] == "MOU-001" and r["tool_trace"][0]["server"] == "shopping_cart"


# 7 ------------------------------------------------------------------------
def test_remove_wireless_mouse_from_cart(client, auth, llm):
    client.post("/api/cart/items", json={"product_id": "MOU-003", "quantity": 1}, headers=auth)
    client.post("/api/cart/items", json={"product_id": "LAP-001", "quantity": 1}, headers=auth)

    def remove_the_mouse(inputs):
        cart = last_outputs(inputs)[0]
        mice = [i for i in cart["items"] if i["category"] == "wireless_mouse"]
        assert len(mice) == 1  # unambiguous here; a real model should ask if there were several
        return tool_calls(call("remove_from_cart", {"product_id": mice[0]["product_id"]}))(inputs)

    llm.script(tool_calls(call("get_cart", {})), remove_the_mouse, text("Confirm removing the mouse."))
    r = chat(client, auth, "Remove the wireless mouse from my cart.")
    assert tools_used(r) == ["get_cart", "remove_from_cart"]
    assert r["pending_approvals"][0]["summary"]["product_id"] == "MOU-003"
    assert len(client.get("/api/cart", headers=auth).json()["items"]) == 2   # not removed until approved


# 8 ------------------------------------------------------------------------
def test_find_laptop_and_prepare_for_checkout(client, auth, llm):
    def add_top_ranked(inputs):
        top = last_outputs(inputs)[0]["ranked"][0]["product_id"]
        return tool_calls(call("add_to_cart", {"product_id": top, "quantity": 1}))(inputs)

    llm.script(tool_calls(call("rank_products", {"category": "laptop", "max_price": 80000, "top_n": 3,
                                                 "use_case": "basic_programming"})),
               add_top_ranked, text("The top pick is ready - please approve adding it."))
    r = chat(client, auth, "Find a laptop and prepare it for checkout.")
    assert tools_used(r) == ["rank_products", "add_to_cart"]
    top = r["ranking"]["ranked"][0]["product_id"]
    assert r["pending_approvals"][0]["arguments"]["product_id"] == top
    # Preparing for checkout never places an order.
    assert client.get("/api/orders", headers=auth).json()["orders"] == []


# 9 ------------------------------------------------------------------------
def test_laptop_under_50000_compare_top_three(client, auth, llm):
    llm.script(tool_calls(call("rank_products", {"category": "laptop", "max_price": 50000, "top_n": 3})),
               text("Top three laptops under ₹50,000."))
    r = chat(client, auth, "Find a laptop under ₹50,000 and compare the top three.")
    ranked = r["ranking"]["ranked"]
    assert len(ranked) == 3 and all(Decimal(x["price"]) <= 50000 for x in ranked)
    assert [x["rank"] for x in ranked] == [1, 2, 3]
    assert r["ranking"]["weights"]["performance"] == 0.4


# 10 -----------------------------------------------------------------------
def test_unrealistic_constraints_no_fabrication(client, auth, llm):
    llm.script(tool_calls(call("rank_products", {"category": "laptop", "max_price": 10000, "min_ram_gb": 64})),
               text("No laptop in the catalog meets both. Would you like to relax the budget or RAM?"))
    r = chat(client, auth, "Find a laptop under ₹10,000 with 64 GB RAM.")
    assert r["ranking"]["no_match"] is True and r["ranking"]["ranked"] == []
    assert r["products"] == []
    assert last_outputs(llm.inputs_seen[1])[0]["no_match"] is True


# ---------------------------------------------------------------- loop mechanics
def test_multiple_tool_calls_in_one_response(client, auth, llm):
    llm.script(tool_calls(call("get_product_details", {"product_id": "LAP-001"}),
                          call("check_inventory", {"product_id": "LAP-010", "requested_quantity": 1})),
               text("Done."))
    r = chat(client, auth, "Details of LAP-001 and is LAP-010 available?")
    assert tools_used(r) == ["get_product_details", "check_inventory"]
    outs = last_outputs(llm.inputs_seen[1])
    assert outs[0]["product"]["product_id"] == "LAP-001" and outs[1]["in_stock"] is False


def test_model_recovers_from_invalid_arguments(client, auth, llm):
    llm.script(tool_calls(call("search_products", {"category": "laptop", "limit": 500})),
               tool_calls(call("search_products", {"category": "laptop", "limit": 5})), text("ok"))
    r = chat(client, auth, "Show laptops")
    assert [t["status"] for t in r["tool_trace"]] == ["rejected", "ok"]
    assert last_outputs(llm.inputs_seen[1])[0]["error"]["code"] == "invalid_arguments"


def test_max_tool_rounds_enforced(client, auth, llm, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "max_tool_rounds", 3)
    llm.script(*[tool_calls(call("search_products", {"category": "laptop"})) for _ in range(10)], text("final"))
    r = chat(client, auth, "loop forever")
    assert sum(1 for t in r["tool_trace"] if t["tool"] == "search_products") == 3
    assert r["tool_trace"][-1]["status"] == "limit" and llm.tool_choices[-1] == "none"


def test_openai_failure_is_reported_honestly(client, auth, llm):
    llm.raise_error = LLMError("rate_limited", "OpenAI rate limit or quota exceeded.", True)
    r = chat(client, auth, "Find a mouse")
    assert r["error"]["code"] == "rate_limited" and "failed" in r["reply"]
    assert r["products"] == [] and r["tool_trace"] == []


def test_prompt_injection_cannot_bypass_approval(client, auth, llm):
    # Even if a (compromised) model obeyed the injected text in ACC-005's description...
    llm.script(tool_calls(call("add_to_cart", {"product_id": "ACC-005", "quantity": 10})), text("..."))
    r = chat(client, auth, "Tell me about ACC-005")
    assert r["pending_approvals"] and client.get("/api/cart", headers=auth).json()["items"] == []


def test_place_order_only_after_explicit_approval(client, auth, llm):
    client.post("/api/cart/items", json={"product_id": "MOU-001", "quantity": 1}, headers=auth)
    llm.script(tool_calls(call("place_order", {})), text("Please confirm the order summary."))
    r = chat(client, auth, "Place my order")
    summary = r["pending_approvals"][0]["summary"]
    assert summary["estimated_total"] == "899.00" and summary["simulated"] is True
    assert client.get("/api/orders", headers=auth).json()["orders"] == []


# ---------------------------------------------------------------- memory
def test_follow_up_uses_conversation_memory(client, auth, llm):
    llm.script(tool_calls(call("search_products", {"category": "laptop", "max_price": 80000, "query": "programming"})),
               text("Here are laptops under ₹80,000."))
    r1 = chat(client, auth, "I need a laptop under ₹80,000 for programming.")
    cid = r1["conversation_id"]

    llm.script(text("Comparing the top three from before."))
    chat(client, auth, "Now compare the top three.", cid)
    sent = llm.inputs_seen[0]
    contents = json.dumps(sent, ensure_ascii=False)
    assert "I need a laptop under ₹80,000 for programming." in contents      # transcript
    state = next(i for i in sent if i.get("role") == "developer")
    assert '"max_price": 80000' in state["content"] and "last_search" in state["content"]  # structured memory

    # A new conversation does not inherit those constraints.
    llm.script(text("Hi!"))
    chat(client, auth, "hello")
    assert not any(i.get("role") == "developer" for i in llm.inputs_seen[0])


def test_conversations_are_isolated_between_users(client, auth, llm):
    llm.script(text("hi"))
    cid = chat(client, auth, "hello")["conversation_id"]
    bob = login(client, "bob")
    assert client.get(f"/api/conversations/{cid}/messages", headers=bob).status_code == 404
    r = client.post("/api/chat", json={"message": "x", "conversation_id": cid}, headers=bob)
    assert r.status_code == 404
    msgs = client.get(f"/api/conversations/{cid}/messages", headers=auth).json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
