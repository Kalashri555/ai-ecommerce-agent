"""MCP client side: connect to the Product Catalog and Shopping Cart servers.

What this module does
---------------------
1. Launches each MCP server as a subprocess and connects over **stdio**
   (`mcp.Client(StdioServerParameters(...))`).
2. Performs the MCP handshake and **discovers tools** (`list_tools`), keeping
   each tool's description and JSON input schema.
3. Converts discovered tools to OpenAI function-tool definitions.
4. Routes `call_tool` to the server that owns the tool, attaching the signed
   user context in the MCP request `_meta`.
5. Handles timeouts, disconnections (one reconnect attempt) and shutdown.

Each connection lives in its own background task, because the SDK's async
context managers must be entered and exited by the same task.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from mcp import Client, StdioServerParameters
from mcp.types import TextContent

from app.config import PROJECT_ROOT, get_settings

log = logging.getLogger("app.mcp")


class MCPUnavailable(Exception):
    pass


@dataclass
class DiscoveredTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    server: str


@dataclass
class MCPCallResult:
    server: str
    tool: str
    ok: bool
    data: dict[str, Any]
    duration_ms: int
    # "succeeded" | "failed" | "unknown" (e.g. timeout during a write)
    outcome: str = "succeeded"


@dataclass
class ServerConnection:
    name: str
    target: Any  # StdioServerParameters, or an in-process MCPServer (tests)
    client: Client | None = None
    tools: list[DiscoveredTool] = field(default_factory=list)
    error: str | None = None
    server_info: str | None = None
    _task: asyncio.Task | None = None
    _ready: asyncio.Event = field(default_factory=asyncio.Event)
    _stop: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def connected(self) -> bool:
        return self.client is not None and self.error is None

    async def start(self, timeout: float) -> None:
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self.error = None
        self._task = asyncio.create_task(self._run(), name=f"mcp-{self.name}")
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
        except asyncio.TimeoutError:
            self.error = f"Timed out after {timeout:.0f}s connecting to MCP server '{self.name}'."
            await self.stop()

    async def _run(self) -> None:
        try:
            async with Client(self.target, read_timeout_seconds=get_settings().tool_timeout_seconds) as client:
                listed = await client.list_tools()
                self.tools = [DiscoveredTool(name=t.name, description=t.description or "",
                                             input_schema=dict(t.input_schema or {"type": "object", "properties": {}}),
                                             server=self.name) for t in listed.tools]
                info = client.server_info
                self.server_info = f"{info.name} {info.version}" if info else self.name
                self.client = client
                self._ready.set()
                await self._stop.wait()
        except Exception as exc:  # connection failure, server crash, ...
            log.warning("MCP server %s failed: %s", self.name, exc)
            self.error = f"MCP server '{self.name}' is unavailable: {type(exc).__name__}: {exc}"
        finally:
            self.client = None
            self._ready.set()

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (asyncio.TimeoutError, Exception):  # pragma: no cover
                self._task.cancel()
            self._task = None
        self.client = None


def _stdio_params(module: str) -> StdioServerParameters:
    s = get_settings()
    env = {
        "DATABASE_URL": s.database_url,
        "APP_SECRET_KEY": s.app_secret_key,
        "PYTHONPATH": os.pathsep.join([str(PROJECT_ROOT / "backend"), str(PROJECT_ROOT)]),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
    }
    return StdioServerParameters(command=sys.executable, args=["-m", module], env=env, cwd=str(PROJECT_ROOT))


def default_targets() -> dict[str, Any]:
    return {
        "product_catalog": _stdio_params("mcp_servers.product_catalog.server"),
        "shopping_cart": _stdio_params("mcp_servers.shopping_cart.server"),
    }


class MCPClientManager:
    """Owns the connections to all MCP servers."""

    def __init__(self, targets: dict[str, Any] | None = None) -> None:
        self.connections = {name: ServerConnection(name, target) for name, target in (targets or default_targets()).items()}
        self._tool_index: dict[str, DiscoveredTool] = {}
        self._lock = asyncio.Lock()

    # ---------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        timeout = get_settings().mcp_startup_timeout_seconds
        await asyncio.gather(*(c.start(timeout) for c in self.connections.values()))
        self._rebuild_index()

    async def stop(self) -> None:
        await asyncio.gather(*(c.stop() for c in self.connections.values()), return_exceptions=True)

    async def reconnect(self, server: str) -> None:
        async with self._lock:
            conn = self.connections[server]
            await conn.stop()
            await conn.start(get_settings().mcp_startup_timeout_seconds)
            self._rebuild_index()

    def _rebuild_index(self) -> None:
        index: dict[str, DiscoveredTool] = {}
        for conn in self.connections.values():
            for tool in conn.tools if conn.connected else []:
                if tool.name in index:
                    log.error("Tool name collision: %s on %s and %s", tool.name, index[tool.name].server, conn.name)
                    continue
                index[tool.name] = tool
        self._tool_index = index

    # ---------------------------------------------------------------- discovery
    def discovered_tools(self) -> list[DiscoveredTool]:
        return list(self._tool_index.values())

    def server_for(self, tool_name: str) -> str | None:
        t = self._tool_index.get(tool_name)
        return t.server if t else None

    def status(self) -> dict[str, Any]:
        return {name: {"connected": c.connected, "server_info": c.server_info, "error": c.error,
                       "tools": [t.name for t in c.tools] if c.connected else []}
                for name, c in self.connections.items()}

    # ---------------------------------------------------------------- calls
    async def call_tool(self, name: str, arguments: dict[str, Any], meta: dict[str, Any], *,
                        idempotent: bool, timeout: float | None = None) -> MCPCallResult:
        tool = self._tool_index.get(name)
        server = tool.server if tool else (self._known_server_for(name) or "unknown")
        conn = self.connections.get(server)
        if conn is None:
            raise MCPUnavailable(f"No MCP server provides tool '{name}'.")
        if not conn.connected:
            # One reconnect attempt before giving up.
            await self.reconnect(server)
            if not conn.connected:
                raise MCPUnavailable(conn.error or f"MCP server '{server}' is not connected.")

        timeout = timeout or get_settings().tool_timeout_seconds
        start = time.perf_counter()
        try:
            assert conn.client is not None
            result = await asyncio.wait_for(conn.client.call_tool(name, arguments, meta=meta), timeout)
        except asyncio.TimeoutError:
            ms = int((time.perf_counter() - start) * 1000)
            return MCPCallResult(server, name, False, _err("tool_timeout",
                                 f"The {name} tool timed out after {timeout:.0f}s.",
                                 outcome="failed" if idempotent else "unknown"), ms,
                                 outcome="failed" if idempotent else "unknown")
        except Exception as exc:
            ms = int((time.perf_counter() - start) * 1000)
            log.warning("MCP call %s failed: %s", name, exc)
            conn.error = f"Connection error: {type(exc).__name__}"
            if idempotent:
                # Safe to retry reads once after reconnecting.
                await self.reconnect(server)
                if conn.connected and conn.client is not None:
                    try:
                        result = await asyncio.wait_for(conn.client.call_tool(name, arguments, meta=meta), timeout)
                        return self._to_result(server, name, result, start)
                    except Exception:  # pragma: no cover
                        pass
            return MCPCallResult(server, name, False, _err("mcp_connection_error",
                                 f"Lost connection to the {server} MCP server.",
                                 outcome="failed" if idempotent else "unknown"), ms,
                                 outcome="failed" if idempotent else "unknown")
        return self._to_result(server, name, result, start)

    def _known_server_for(self, name: str) -> str | None:
        for conn in self.connections.values():
            if any(t.name == name for t in conn.tools):
                return conn.name
        return None

    @staticmethod
    def _to_result(server: str, name: str, result, start: float) -> MCPCallResult:
        ms = int((time.perf_counter() - start) * 1000)
        text = "".join(b.text for b in result.content if isinstance(b, TextContent))
        if result.is_error:
            try:
                # The SDK prefixes "Error executing tool X: " - parse the JSON part.
                data = json.loads(text[text.index("{"):]) if "{" in text else None
                if not isinstance(data, dict):
                    raise ValueError
            except ValueError:
                data = _err("tool_error", text or "The tool reported an error.")
            data.setdefault("ok", False)
            return MCPCallResult(server, name, False, data, ms, outcome=data.get("outcome", "failed"))
        data = result.structured_content
        if not isinstance(data, dict):
            try:
                data = json.loads(text)
            except ValueError:
                data = {"ok": True, "text": text}
        return MCPCallResult(server, name, bool(data.get("ok", True)), data, ms)


def _err(code: str, message: str, outcome: str = "failed") -> dict[str, Any]:
    return {"ok": False, "outcome": outcome, "error": {"code": code, "message": message, "details": {}}}


def to_openai_tool(tool: DiscoveredTool) -> dict[str, Any]:
    """MCP tool definition -> OpenAI Responses API function tool."""
    schema = json.loads(json.dumps(tool.input_schema))  # deep copy
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    schema.pop("title", None)
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description.strip()[:1000],
        "parameters": schema,
        # Non-strict: optional parameters stay optional. The backend validates
        # every argument with Pydantic before anything is executed.
        "strict": False,
    }
