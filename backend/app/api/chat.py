"""Chat, approvals, conversations, auth, tools and health endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import approval_service, conversation_service as convs
from app.api.deps import chat_rate_limit, get_agent, get_executor
from app.auth.dependencies import CurrentUser, get_current_user, issue_token
from app.auth.passwords import verify_password
from app.config import get_settings
from app.database.connection import get_db, session_scope
from app.database.models import User
from app.database.schemas import (ApprovalDecisionRequest, ApprovalDecisionResponse, ChatRequest, ChatResponse,
                                  LoginRequest, LoginResponse, UserOut)
from app.services.errors import ServiceError

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------ auth
@router.post("/auth/login", response_model=LoginResponse, tags=["auth"])
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == body.username.strip().lower()))
    if user is None or not verify_password(body.password, user.password_hash):
        raise ServiceError("invalid_credentials", "Invalid username or password.", http_status=401)
    return LoginResponse(token=issue_token(user), user=UserOut(id=user.id, username=user.username,
                                                                display_name=user.display_name))


@router.get("/auth/me", response_model=UserOut, tags=["auth"])
def me(user: CurrentUser = Depends(get_current_user)):
    return UserOut(id=user.id, username=user.username, display_name=user.display_name)


# ------------------------------------------------------------------ health / tools
@router.get("/health", tags=["system"])
def health(request: Request):
    s = get_settings()
    mcp = request.app.state.mcp
    return {"status": "ok", "app_env": s.app_env, "openai_configured": s.openai_configured,
            "openai_model": s.openai_model or None, "mcp": mcp.status(),
            "tools_available": len(request.app.state.registry.openai_tools()),
            "max_tool_rounds": s.max_tool_rounds}


@router.get("/tools", tags=["system"])
def tools(request: Request, user: CurrentUser = Depends(get_current_user)):
    """The tool definitions the agent currently offers to the model (discovered via MCP)."""
    return {"tools": request.app.state.registry.describe()}


# ------------------------------------------------------------------ chat
@router.post("/chat", response_model=ChatResponse, tags=["chat"])
async def chat(body: ChatRequest, user: CurrentUser = Depends(chat_rate_limit), agent=Depends(get_agent)):
    result = await agent.run_turn(user.id, body.conversation_id, body.message.strip())
    return ChatResponse(**result.to_dict())


# ------------------------------------------------------------------ approvals
@router.get("/approvals", tags=["approvals"])
def pending_approvals(conversation_id: str | None = None, user: CurrentUser = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    out = approval_service.list_pending(db, user.id, conversation_id)
    db.commit()
    return {"approvals": out}


async def _approve_and_execute(approval_id: str, user: CurrentUser, executor, agent, continue_agent: bool):
    with session_scope() as db:
        a = approval_service.approve(db, approval_id, user.id)   # pending -> approved (once)
        approval = approval_service.to_dict(a)
    result = await executor.execute_approved(approval, user.id)
    with session_scope() as db:
        approval = approval_service.to_dict(approval_service.get_owned(db, approval_id, user.id))

    agent_resp = None
    if approval["conversation_id"]:
        status = result.get("approval_status")
        if status == "executed":
            event = (f"The user APPROVED {approval['action_type']} ({approval['summary'].get('title')}). "
                     f"It was executed successfully. Result: {_brief(result)}")
        else:
            msg = result.get("error", {}).get("message") or result.get("message", "")
            event = (f"The user approved {approval['action_type']} ({approval['summary'].get('title')}) but it was "
                     f"NOT completed (status: {status}). Reason: {msg}")
        if continue_agent and agent.llm_available():
            turn = await agent.run_turn(user.id, approval["conversation_id"], None,
                                        event=event + " Continue the user's earlier request if steps remain "
                                        "(e.g. show the updated cart); otherwise confirm the outcome in one or two sentences.")
            agent_resp = ChatResponse(**turn.to_dict())
        else:
            with session_scope() as db:
                conv = convs.get_owned(db, user.id, approval["conversation_id"])
                convs.add_message(db, conv, "event", event)
    return ApprovalDecisionResponse(approval=approval, result=result, agent=agent_resp)


def _brief(result: dict) -> str:
    keys = ("product_id", "product_name", "quantity_in_cart", "new_quantity", "removed_quantity", "order_id", "total")
    parts = {k: result[k] for k in keys if k in result}
    if "cart" in result:
        parts["cart_subtotal"] = result["cart"].get("subtotal")
        parts["cart_items"] = result["cart"].get("item_count")
    return str(parts)


@router.post("/approvals/{approval_id}/approve", response_model=ApprovalDecisionResponse, tags=["approvals"])
async def approve(approval_id: str, body: ApprovalDecisionRequest | None = None,
                  user: CurrentUser = Depends(get_current_user), executor=Depends(get_executor), agent=Depends(get_agent)):
    return await _approve_and_execute(approval_id, user, executor, agent, (body or ApprovalDecisionRequest()).continue_agent)


@router.post("/approvals/{approval_id}/cancel", response_model=ApprovalDecisionResponse, tags=["approvals"])
def cancel(approval_id: str, user: CurrentUser = Depends(get_current_user)):
    with session_scope() as db:
        a = approval_service.cancel(db, approval_id, user.id)
        approval = approval_service.to_dict(a)
        if approval["conversation_id"]:
            conv = convs.get_owned(db, user.id, approval["conversation_id"])
            convs.add_message(db, conv, "event", f"The user CANCELLED {approval['action_type']} "
                              f"({approval['summary'].get('title')}). Nothing was changed.")
    return ApprovalDecisionResponse(approval=approval, result={"ok": True, "approval_status": "cancelled"})


# ------------------------------------------------------------------ conversations
@router.get("/conversations", tags=["conversations"])
def list_conversations(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return {"conversations": convs.list_conversations(db, user.id)}


@router.get("/conversations/{conversation_id}", tags=["conversations"])
def get_conversation(conversation_id: str, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    c = convs.get_owned(db, user.id, conversation_id)
    return {"conversation_id": c.id, "title": c.title, "state": convs.load_state(c),
            "created_at": c.created_at.isoformat(), "updated_at": c.updated_at.isoformat()}


@router.get("/conversations/{conversation_id}/messages", tags=["conversations"])
def get_messages(conversation_id: str, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    c = convs.get_owned(db, user.id, conversation_id)
    return {"conversation_id": c.id, "messages": [convs.serialize_message(m) for m in c.messages]}
