"""Conversation persistence and memory.

Two kinds of memory are kept per conversation:

1. **Transcript** - user / assistant / event messages in `conversation_messages`.
   Only the last N are sent to the model (HISTORY_MESSAGE_LIMIT).
2. **Structured state** - a small JSON object in `conversations.state_json`
   with the last search filters, the last ranking criteria and the products
   recently shown. Follow-ups like "now compare the top three" resolve against
   this state instead of a long transcript.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.models import Conversation, ConversationMessage, utcnow
from app.services.errors import NotFoundError


def get_or_create(db: Session, user_id: int, conversation_id: str | None) -> Conversation:
    if conversation_id:
        conv = db.get(Conversation, conversation_id)
        if conv is None or conv.user_id != user_id:  # isolation: never reveal others' conversations
            raise NotFoundError("conversation_not_found", "Conversation not found. Start a new conversation.")
        return conv
    conv = Conversation(id=str(uuid.uuid4()), user_id=user_id, title="New conversation", state_json="{}")
    db.add(conv)
    db.flush()
    return conv


def get_owned(db: Session, user_id: int, conversation_id: str) -> Conversation:
    return get_or_create(db, user_id, conversation_id)


def add_message(db: Session, conv: Conversation, role: str, text: str, metadata: dict[str, Any] | None = None) -> ConversationMessage:
    msg = ConversationMessage(conversation_id=conv.id, role=role, message_text=text,
                              metadata_json=json.dumps(metadata or {}, default=str))
    db.add(msg)
    if role == "user" and conv.title == "New conversation":
        conv.title = (text[:60] + "...") if len(text) > 60 else text
    conv.updated_at = utcnow()
    db.flush()
    return msg


def load_state(conv: Conversation) -> dict[str, Any]:
    try:
        return json.loads(conv.state_json or "{}")
    except json.JSONDecodeError:
        return {}


def save_state(db: Session, conv: Conversation, state: dict[str, Any]) -> None:
    conv.state_json = json.dumps(state, default=str)
    db.flush()


def history_for_model(db: Session, conv: Conversation) -> list[dict[str, Any]]:
    """Last N messages as Responses API input items."""
    limit = get_settings().history_message_limit
    rows = list(db.scalars(select(ConversationMessage).where(ConversationMessage.conversation_id == conv.id)
                           .order_by(ConversationMessage.id.desc()).limit(limit)))
    rows.reverse()
    items = []
    for m in rows:
        if m.role == "user":
            items.append({"role": "user", "content": m.message_text})
        elif m.role == "assistant":
            items.append({"role": "assistant", "content": m.message_text})
        elif m.role == "event":
            items.append({"role": "developer", "content": f"[Application event] {m.message_text}"})
    return items


def update_state_from_tool(state: dict[str, Any], tool: str, args: dict[str, Any], data: dict[str, Any]) -> None:
    """Fold a successful tool result into structured memory."""
    if not data.get("ok", True):
        return
    shown: dict[str, str] = state.get("recent_products", {})

    def remember(products: list[dict[str, Any]]) -> None:
        for p in products:
            shown[p["product_id"]] = p.get("name", "")
        # keep the 15 most recent
        state["recent_products"] = dict(list(shown.items())[-15:])

    if tool == "search_products":
        state["last_search"] = {"filters": args, "result_ids": [p["product_id"] for p in data["products"]],
                                "total_matches": data["total_matches"]}
        remember(data["products"])
    elif tool == "rank_products":
        state["last_ranking"] = {"criteria": args, "ranked_ids": [r["product_id"] for r in data.get("ranked", [])],
                                 "no_match": data.get("no_match", False)}
        remember(data.get("ranked", []))
    elif tool == "compare_products":
        state["last_compared_ids"] = [p["product_id"] for p in data["products"]]
        remember(data["products"])
    elif tool == "get_product_details":
        remember([data["product"]])
    elif tool in ("get_cart", "add_to_cart", "update_cart", "remove_from_cart"):
        cart = data if tool == "get_cart" else data.get("cart", {})
        state["cart_snapshot"] = [{"product_id": i["product_id"], "name": i["name"], "quantity": i["quantity"]}
                                  for i in cart.get("items", [])]


def state_message(state: dict[str, Any]) -> dict[str, Any] | None:
    if not state:
        return None
    return {"role": "developer",
            "content": ("Structured memory for THIS conversation (from earlier tool results; may be stale - re-check "
                        "prices/stock with tools before acting):\n" + json.dumps(state, default=str))}


def serialize_message(m: ConversationMessage) -> dict[str, Any]:
    try:
        meta = json.loads(m.metadata_json or "{}")
    except json.JSONDecodeError:
        meta = {}
    return {"id": m.id, "role": m.role, "text": m.message_text, "metadata": meta, "created_at": m.created_at.isoformat()}


def list_conversations(db: Session, user_id: int) -> list[dict[str, Any]]:
    rows = db.scalars(select(Conversation).where(Conversation.user_id == user_id)
                      .order_by(Conversation.updated_at.desc()).limit(50)).all()
    return [{"conversation_id": c.id, "title": c.title, "created_at": c.created_at.isoformat(),
             "updated_at": c.updated_at.isoformat()} for c in rows]
