"""FastAPI application entry point.

Start (from the backend folder, with the virtual environment active):
    python -m uvicorn app.main:app --port 8000

On startup the app: creates tables, seeds demo data if the catalog is empty,
launches both MCP servers as subprocesses (stdio), discovers their tools and
wires up the agent. On shutdown it closes the MCP connections.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.agent.agent_service import AgentService
from app.agent.mcp_client import MCPClientManager
from app.agent.openai_client import OpenAIResponsesClient
from app.agent.tool_executor import ToolExecutor
from app.agent.tool_registry import ToolRegistry
from app.api import cart, chat, products
from app.config import get_settings
from app.database.connection import init_db, session_scope
from app.database.models import Product
from app.services.errors import ServiceError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


def _ensure_seeded() -> None:
    init_db()
    with session_scope() as db:
        count = db.scalar(select(func.count(Product.id)))
    if not count:
        from app.database.seed import seed

        log.info("Empty catalog - seeding demo data: %s", seed())


def build_app(mcp_targets: dict | None = None, llm_factory=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _ensure_seeded()
        mcp = MCPClientManager(mcp_targets)
        await mcp.start()
        for name, st in mcp.status().items():
            if st["connected"]:
                log.info("MCP server %s connected (%s): %d tools", name, st["server_info"], len(st["tools"]))
            else:
                log.error("MCP server %s NOT connected: %s", name, st["error"])
        registry = ToolRegistry(mcp)
        executor = ToolExecutor(mcp, registry)
        app.state.mcp = mcp
        app.state.registry = registry
        app.state.executor = executor
        app.state.agent = AgentService(llm_factory or OpenAIResponsesClient, executor, registry)
        if not get_settings().openai_configured:
            log.warning("OPENAI_API_KEY / OPENAI_MODEL not set - chat will report that the model is unavailable.")
        try:
            yield
        finally:
            await mcp.stop()

    app = FastAPI(title="AI E-Commerce Agent", version="1.0.0", lifespan=lifespan,
                  description="Learning project: OpenAI tool calling + MCP + agentic workflows (demo data).")
    app.add_middleware(CORSMiddleware, allow_origins=[get_settings().frontend_origin, "http://127.0.0.1:5173"],
                       allow_credentials=False, allow_methods=["*"], allow_headers=["Authorization", "Content-Type"])

    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, exc: ServiceError):
        return JSONResponse(status_code=exc.http_status, content={"error": {"code": exc.code, "message": exc.message,
                                                                            "details": exc.details}})

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "http_error", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={"error": detail}, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        issues = [{"field": ".".join(str(x) for x in e["loc"]), "message": e["msg"]} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"error": {"code": "invalid_request", "message": "Invalid request.",
                                                                "details": {"issues": issues}}})

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception):  # never leak internals
        log.exception("Unhandled error")
        return JSONResponse(status_code=500, content={"error": {"code": "internal_error",
                                                                "message": "Something went wrong on the server."}})

    app.include_router(chat.router)
    app.include_router(products.router)
    app.include_router(cart.router)

    @app.get("/", include_in_schema=False)
    def root():
        return {"app": "AI E-Commerce Agent", "docs": "/docs", "health": "/api/health"}

    return app


app = build_app()
