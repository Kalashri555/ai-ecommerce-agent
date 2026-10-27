"""Thin wrapper around the official OpenAI Python SDK (Responses API).

Kept behind a tiny interface (`LLMClient.create`) so tests can swap in a fake
model and run without an API key.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from app.config import get_settings

log = logging.getLogger("app.llm")


class LLMError(Exception):
    """Model call failed. `code` is safe to show; `retryable` for the caller."""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass
class FunctionCall:
    call_id: str
    name: str
    arguments: str


@dataclass
class LLMTurn:
    output_items: list[dict[str, Any]]   # raw output items to append to the next input
    function_calls: list[FunctionCall]
    text: str                            # assistant text in this response (may be "")
    response_id: str | None = None
    usage: dict[str, Any] | None = None


class LLMClient(Protocol):
    async def create(self, *, instructions: str, input_items: list[dict[str, Any]], tools: list[dict[str, Any]],
                     tool_choice: str = "auto") -> LLMTurn: ...


class OpenAIResponsesClient:
    """Calls `client.responses.create(...)` with function tools."""

    def __init__(self, http_client: Any = None) -> None:
        from openai import AsyncOpenAI

        s = get_settings()
        if not s.openai_configured:
            raise LLMError("not_configured", "OPENAI_API_KEY and OPENAI_MODEL must be set in .env.")
        # The SDK retries connection errors, 408/409/429 and 5xx with exponential
        # backoff; we bound it with max_retries and a request timeout.
        self._client = AsyncOpenAI(api_key=s.openai_api_key, timeout=s.openai_timeout_seconds,
                                   max_retries=s.openai_max_retries, http_client=http_client)
        self._model = s.openai_model

    async def create(self, *, instructions: str, input_items: list[dict[str, Any]], tools: list[dict[str, Any]],
                     tool_choice: str = "auto") -> LLMTurn:
        import openai

        try:
            resp = await self._client.responses.create(
                model=self._model,
                instructions=instructions,
                input=input_items,
                tools=tools,
                tool_choice=tool_choice,
                parallel_tool_calls=True,
            )
        except openai.AuthenticationError as exc:
            raise LLMError("auth_error", "OpenAI rejected the API key. Check OPENAI_API_KEY.") from exc
        except openai.RateLimitError as exc:
            raise LLMError("rate_limited", "OpenAI rate limit or quota exceeded. Please wait and retry.", True) from exc
        except openai.NotFoundError as exc:
            raise LLMError("model_not_found", f"Model '{self._model}' was not found or is not available to this key.") from exc
        except openai.BadRequestError as exc:
            log.warning("OpenAI bad request: %s", exc)
            raise LLMError("bad_request", "OpenAI rejected the request (see server logs).") from exc
        except openai.APITimeoutError as exc:
            raise LLMError("timeout", "OpenAI did not respond in time.", True) from exc
        except openai.APIConnectionError as exc:
            raise LLMError("connection_error", "Could not reach the OpenAI API.", True) from exc
        except openai.APIError as exc:
            raise LLMError("api_error", "The OpenAI API returned an error.", True) from exc

        return parse_response(resp)


def parse_response(resp: Any) -> LLMTurn:
    items: list[dict[str, Any]] = []
    calls: list[FunctionCall] = []
    texts: list[str] = []
    for item in resp.output or []:
        dumped = item.model_dump(exclude_none=True) if hasattr(item, "model_dump") else dict(item)
        items.append(dumped)
        itype = dumped.get("type")
        if itype == "function_call":
            calls.append(FunctionCall(call_id=dumped["call_id"], name=dumped["name"], arguments=dumped.get("arguments", "{}")))
        elif itype == "message":
            for part in dumped.get("content", []):
                if part.get("type") == "output_text":
                    texts.append(part.get("text", ""))
        # other item types (e.g. reasoning) are passed back unchanged
    usage = resp.usage.model_dump() if getattr(resp, "usage", None) is not None and hasattr(resp.usage, "model_dump") else None
    return LLMTurn(output_items=items, function_calls=calls, text="\n".join(t for t in texts if t).strip(),
                   response_id=getattr(resp, "id", None), usage=usage)
