"""Shared pytest fixtures.

* Uses a temporary SQLite file (never your real ecommerce.db).
* MCP servers run IN-PROCESS for most tests (`mcp.Client(MCPServer)`), which
  still exercises the real MCP protocol (handshake, list_tools, call_tool,
  _meta) without subprocesses. `test_mcp_stdio.py` launches real subprocesses.
* The OpenAI model is replaced by `ScriptedLLM`, so no API key is needed.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = PROJECT_ROOT / "backend"
for p in (str(BACKEND_DIR), str(PROJECT_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

_TMP = tempfile.mkdtemp(prefix="ecom-tests-")
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(_TMP) / "test.db").replace("\\", "/")
os.environ["APP_SECRET_KEY"] = "test-secret-key-0123456789abcdef"
# Keep any real key aside for the optional live test, then disable it for the suite.
os.environ.setdefault("LIVE_OPENAI_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
os.environ.setdefault("LIVE_OPENAI_MODEL", os.environ.get("OPENAI_MODEL", ""))
os.environ["OPENAI_API_KEY"] = ""
os.environ["OPENAI_MODEL"] = ""
os.environ["CHAT_RATE_LIMIT_PER_MINUTE"] = "1000"

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.agent.openai_client import FunctionCall, LLMError, LLMTurn  # noqa: E402
from app.database.connection import configure_engine  # noqa: E402
from app.database.seed import seed  # noqa: E402

configure_engine(os.environ["DATABASE_URL"])
seed(reset=True)


@pytest.fixture
def fresh_db():
    """Reset the database to the seeded state."""
    seed(reset=True)
    yield


def in_process_targets() -> dict[str, Any]:
    from mcp_servers.product_catalog.server import mcp as catalog
    from mcp_servers.shopping_cart.server import mcp as cart

    return {"product_catalog": catalog, "shopping_cart": cart}


# ---------------------------------------------------------------------------
# Scripted fake model
# ---------------------------------------------------------------------------
Step = Callable[[list[dict[str, Any]]], LLMTurn]


def call(name: str, args: dict[str, Any] | str) -> tuple[str, Any]:
    return (name, args)


def tool_calls(*calls: tuple[str, Any]) -> Step:
    def step(_inputs):
        fcs, items = [], []
        for i, (name, args) in enumerate(calls):
            cid = f"call_{name}_{i}_{len(_inputs)}"
            arg_str = args if isinstance(args, str) else json.dumps(args)
            fcs.append(FunctionCall(call_id=cid, name=name, arguments=arg_str))
            items.append({"type": "function_call", "call_id": cid, "name": name, "arguments": arg_str})
        return LLMTurn(output_items=items, function_calls=fcs, text="")
    return step


def text(msg: str) -> Step:
    return lambda _inputs: LLMTurn(output_items=[{"type": "message", "role": "assistant",
                                                  "content": [{"type": "output_text", "text": msg}]}],
                                   function_calls=[], text=msg)


def last_outputs(inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tool outputs fed back to the model in the most recent round."""
    outs = []
    for item in reversed(inputs):
        if item.get("type") == "function_call_output":
            outs.append(json.loads(item["output"]))
        elif item.get("type") == "function_call":
            break
    return list(reversed(outs))


class ScriptedLLM:
    """Plays back a list of steps; each step may inspect the inputs it receives
    (e.g. to pick a product from the previous tool result)."""

    def __init__(self) -> None:
        self.steps: list[Step] = []
        self.inputs_seen: list[list[dict[str, Any]]] = []
        self.tools_seen: list[list[dict[str, Any]]] = []
        self.tool_choices: list[str] = []
        self.raise_error: LLMError | None = None

    def script(self, *steps: Step) -> "ScriptedLLM":
        self.steps = list(steps)
        self.inputs_seen.clear()
        return self

    async def create(self, *, instructions, input_items, tools, tool_choice="auto") -> LLMTurn:
        if self.raise_error:
            raise self.raise_error
        self.inputs_seen.append(list(input_items))
        self.tools_seen.append(tools)
        self.tool_choices.append(tool_choice)
        if not self.steps:
            return text("(script exhausted)")(input_items)
        return self.steps.pop(0)(input_items)


@pytest.fixture
def llm() -> ScriptedLLM:
    return ScriptedLLM()


@pytest.fixture
def client(llm, fresh_db):
    from fastapi.testclient import TestClient

    from app.main import build_app

    app = build_app(mcp_targets=in_process_targets(), llm_factory=lambda: llm)
    with TestClient(app) as c:
        yield c


def run(client, fn, *args, **kwargs):
    """Run an async function on the TestClient's event loop (where MCP lives)."""
    import functools

    return client.portal.call(functools.partial(fn, *args, **kwargs))


def login(client, username: str = "demo", password: str | None = None) -> dict[str, str]:
    r = client.post("/api/auth/login", json={"username": username, "password": password or f"{username}123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def auth(client) -> dict[str, str]:
    return login(client, "demo")
