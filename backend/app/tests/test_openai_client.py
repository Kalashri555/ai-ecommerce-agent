"""The real OpenAI SDK client against a mocked HTTP transport (no network, no key).

Verifies the exact Responses API request we build (model, instructions, tools,
function_call_output items) and how function calls / text are parsed.
"""

from __future__ import annotations

import asyncio
import json

import httpx

from app.agent.openai_client import LLMError, OpenAIResponsesClient
from app.config import get_settings


def _response(output):
    return {"id": "resp_test", "object": "response", "created_at": 0, "model": "test-model", "status": "completed",
            "output": output, "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
                      "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}}


def _client(monkeypatch, handler):
    s = get_settings()
    monkeypatch.setattr(s, "openai_api_key", "sk-test")
    monkeypatch.setattr(s, "openai_model", "test-model")
    monkeypatch.setattr(s, "openai_max_retries", 0)
    return OpenAIResponsesClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


TOOLS = [{"type": "function", "name": "search_products", "description": "Search", "strict": False,
          "parameters": {"type": "object", "properties": {"category": {"type": "string"}}}}]


def test_request_shape_and_function_call_parsing(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_response([{"type": "function_call", "id": "fc_1", "call_id": "call_1",
                                                    "name": "search_products", "status": "completed",
                                                    "arguments": json.dumps({"category": "laptop"})}]))

    llm = _client(monkeypatch, handler)
    inputs = [{"role": "user", "content": "laptops"},
              {"type": "function_call", "call_id": "call_0", "name": "get_cart", "arguments": "{}"},
              {"type": "function_call_output", "call_id": "call_0", "output": "{\"ok\": true}"}]
    turn = asyncio.run(llm.create(instructions="SYS", input_items=inputs, tools=TOOLS))

    assert seen["url"].endswith("/responses")
    body = seen["body"]
    assert body["model"] == "test-model" and body["instructions"] == "SYS"
    assert body["tools"][0]["name"] == "search_products" and body["tool_choice"] == "auto"
    assert body["input"][2] == {"type": "function_call_output", "call_id": "call_0", "output": "{\"ok\": true}"}
    assert turn.function_calls[0].name == "search_products"
    assert json.loads(turn.function_calls[0].arguments) == {"category": "laptop"}
    assert turn.output_items[0]["type"] == "function_call"  # replayed into the next request


def test_text_response_parsing(monkeypatch):
    def handler(request):
        return httpx.Response(200, json=_response([{"type": "message", "id": "msg_1", "role": "assistant",
                                                    "status": "completed",
                                                    "content": [{"type": "output_text", "text": "Hello!", "annotations": []}]}]))

    turn = asyncio.run(_client(monkeypatch, handler).create(instructions="", input_items=[], tools=TOOLS))
    assert turn.text == "Hello!" and turn.function_calls == []


def test_api_errors_are_mapped(monkeypatch):
    def handler(request):
        return httpx.Response(429, json={"error": {"message": "slow down", "type": "rate_limit", "code": "rate_limit"}})

    try:
        asyncio.run(_client(monkeypatch, handler).create(instructions="", input_items=[], tools=TOOLS))
        raise AssertionError("expected LLMError")
    except LLMError as exc:
        assert exc.code == "rate_limited" and exc.retryable
