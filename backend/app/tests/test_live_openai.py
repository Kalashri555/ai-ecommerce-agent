"""OPTIONAL live test against the real OpenAI API (costs a few cents at most).

Run (PowerShell):
    $env:RUN_LIVE_OPENAI="1"; python -m pytest -m live_openai -s

Uses OPENAI_API_KEY / OPENAI_MODEL from your environment or .env.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.live_openai

LIVE = os.environ.get("RUN_LIVE_OPENAI") == "1"


@pytest.fixture
def live_client(fresh_db, monkeypatch):
    if not LIVE:
        pytest.skip("set RUN_LIVE_OPENAI=1 to run live OpenAI tests")
    from dotenv import dotenv_values

    from app.config import PROJECT_ROOT, get_settings

    env = {**dotenv_values(PROJECT_ROOT / ".env"), **dotenv_values(PROJECT_ROOT / "backend" / ".env")}
    key = os.environ.get("LIVE_OPENAI_API_KEY") or env.get("OPENAI_API_KEY")
    model = os.environ.get("LIVE_OPENAI_MODEL") or env.get("OPENAI_MODEL")
    if not key or not model:
        pytest.skip("OPENAI_API_KEY and OPENAI_MODEL are required")
    s = get_settings()
    monkeypatch.setattr(s, "openai_api_key", key)
    monkeypatch.setattr(s, "openai_model", model)

    from fastapi.testclient import TestClient

    from app.agent.openai_client import OpenAIResponsesClient
    from app.main import build_app
    from app.tests.conftest import in_process_targets, login

    app = build_app(mcp_targets=in_process_targets(), llm_factory=OpenAIResponsesClient)
    with TestClient(app) as c:
        yield c, login(c)


def test_real_model_selects_search_tool(live_client):
    client, auth = live_client
    r = client.post("/api/chat", json={"message": "Find a wireless mouse under ₹2,000."}, headers=auth).json()
    print("\nTOOLS:", [(t["tool"], t["arguments"]) for t in r["tool_trace"]], "\nREPLY:", r["reply"])
    assert r["error"] is None
    assert "search_products" in [t["tool"] for t in r["tool_trace"]] or "rank_products" in [t["tool"] for t in r["tool_trace"]]
    assert r["products"] or r["ranking"]


def test_real_model_cart_action_requires_approval(live_client):
    client, auth = live_client
    r = client.post("/api/chat", json={"message": "Add LAP-001 to my cart."}, headers=auth).json()
    print("\nTOOLS:", [(t["tool"], t["status"]) for t in r["tool_trace"]], "\nREPLY:", r["reply"])
    assert r["pending_approvals"], "the model should request add_to_cart, which must wait for approval"
    assert client.get("/api/cart", headers=auth).json()["items"] == []
