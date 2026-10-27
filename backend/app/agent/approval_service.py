r"""Human-in-the-loop approvals, enforced in code (not in the prompt).

Lifecycle
---------
    pending --approve--> approved --(MCP server executes)--> executed
       |                     \--(execution error)----------> failed
       +--cancel--> cancelled
       +--timeout--> expired
       +--revalidation fails (price/stock changed)--> invalidated

Guarantees
----------
* An approval belongs to one user; other users get "not found".
* It authorises ONE action with ONE exact payload (SHA-256 payload hash).
* Status transitions use conditional UPDATEs, so a double-click or a replayed
  request cannot approve or execute twice.
* The Shopping Cart MCP server itself calls `consume_approval` inside the same
  DB transaction as the cart mutation - so even a buggy/hostile caller cannot
  mutate a cart without a matching, approved, unconsumed approval.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.models import PendingApproval, utcnow
from app.services.errors import ConflictError, NotFoundError, ServiceError

APPROVAL_REQUIRED_ACTIONS = {"add_to_cart", "update_cart", "remove_from_cart", "place_order"}


class ApprovalExpired(ServiceError):
    http_status = 410


def canonical_args(arguments: dict[str, Any]) -> str:
    return json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)


def payload_hash(user_id: int, action_type: str, arguments: dict[str, Any]) -> str:
    raw = f"{user_id}|{action_type}|{canonical_args(arguments)}"
    return hashlib.sha256(raw.encode()).hexdigest()


def to_dict(a: PendingApproval) -> dict[str, Any]:
    return {
        "approval_id": a.id,
        "action_type": a.action_type,
        "arguments": json.loads(a.action_arguments_json),
        "summary": json.loads(a.summary_json or "{}"),
        "status": a.status,
        "conversation_id": a.conversation_id,
        "created_at": a.created_at.isoformat(),
        "expires_at": a.expires_at.isoformat(),
        "result": json.loads(a.execution_result_json) if a.execution_result_json else None,
    }


def create_approval(db: Session, *, user_id: int, action_type: str, arguments: dict[str, Any],
                    summary: dict[str, Any], conversation_id: str | None = None) -> PendingApproval:
    if action_type not in APPROVAL_REQUIRED_ACTIONS:
        raise ValueError(f"{action_type} does not use approvals")
    now = utcnow()
    approval = PendingApproval(
        id=str(uuid.uuid4()), user_id=user_id, conversation_id=conversation_id, action_type=action_type,
        action_arguments_json=canonical_args(arguments), payload_hash=payload_hash(user_id, action_type, arguments),
        summary_json=json.dumps(summary, default=str), status="pending", created_at=now,
        expires_at=now + timedelta(minutes=get_settings().approval_ttl_minutes),
    )
    db.add(approval)
    db.flush()
    return approval


def get_owned(db: Session, approval_id: str, user_id: int) -> PendingApproval:
    a = db.get(PendingApproval, approval_id)
    if a is None or a.user_id != user_id:  # never reveal other users' approvals
        raise NotFoundError("approval_not_found", "This approval request does not exist.")
    return a


def _expire_if_needed(db: Session, a: PendingApproval) -> None:
    if a.status == "pending" and a.expires_at <= utcnow():
        db.execute(update(PendingApproval).where(PendingApproval.id == a.id, PendingApproval.status == "pending")
                   .values(status="expired", decided_at=utcnow()))
        db.flush()
        db.refresh(a)


def approve(db: Session, approval_id: str, user_id: int) -> PendingApproval:
    """pending -> approved (exactly once)."""
    a = get_owned(db, approval_id, user_id)
    _expire_if_needed(db, a)
    if a.status == "expired":
        raise ApprovalExpired("approval_expired", "This request expired. Please ask again.")
    res = db.execute(
        update(PendingApproval)
        .where(PendingApproval.id == approval_id, PendingApproval.user_id == user_id,
               PendingApproval.status == "pending", PendingApproval.expires_at > utcnow())
        .values(status="approved", decided_at=utcnow())
    )
    if res.rowcount != 1:
        db.refresh(a)
        raise ConflictError("approval_not_pending", f"This request is already {a.status}.", details={"status": a.status})
    db.flush()
    db.refresh(a)
    return a


def cancel(db: Session, approval_id: str, user_id: int) -> PendingApproval:
    a = get_owned(db, approval_id, user_id)
    _expire_if_needed(db, a)
    res = db.execute(update(PendingApproval)
                     .where(PendingApproval.id == approval_id, PendingApproval.status == "pending")
                     .values(status="cancelled", decided_at=utcnow()))
    if res.rowcount != 1:
        db.refresh(a)
        raise ConflictError("approval_not_pending", f"This request is already {a.status}.", details={"status": a.status})
    db.flush()
    db.refresh(a)
    return a


def mark(db: Session, approval_id: str, status: str, result: dict[str, Any] | None = None,
         only_from: tuple[str, ...] = ("approved",)) -> bool:
    res = db.execute(update(PendingApproval)
                     .where(PendingApproval.id == approval_id, PendingApproval.status.in_(only_from))
                     .values(status=status, execution_result_json=json.dumps(result, default=str) if result else None))
    return res.rowcount == 1


def record_result(db: Session, approval_id: str, result: dict[str, Any]) -> None:
    a = db.get(PendingApproval, approval_id)
    if a is not None:
        a.execution_result_json = json.dumps(result, default=str)
        db.flush()


def consume_approval(db: Session, *, approval_id: str | None, user_id: int, action_type: str,
                     arguments: dict[str, Any]) -> None:
    """Called by the Shopping Cart MCP server, inside the mutation's transaction.

    approved -> executed, only if user, action and exact payload match. If the
    mutation later fails, the transaction rolls back and the approval stays
    'approved' until the backend marks it 'failed'.
    """
    if not approval_id:
        raise ServiceError("approval_required",
                           f"{action_type} changes your cart/order and needs your explicit approval first.",
                           http_status=403)
    expected = payload_hash(user_id, action_type, arguments)
    res = db.execute(
        update(PendingApproval)
        .where(PendingApproval.id == approval_id, PendingApproval.user_id == user_id,
               PendingApproval.action_type == action_type, PendingApproval.payload_hash == expected,
               PendingApproval.status == "approved")
        .values(status="executed")
    )
    if res.rowcount != 1:
        a = db.get(PendingApproval, approval_id)
        state = "missing" if a is None or a.user_id != user_id else a.status
        raise ServiceError("approval_invalid",
                           "No matching approved request was found (it may be for a different action, "
                           "already used, cancelled or expired).", details={"approval_state": state}, http_status=403)


def list_pending(db: Session, user_id: int, conversation_id: str | None = None) -> list[dict[str, Any]]:
    stmt = select(PendingApproval).where(PendingApproval.user_id == user_id, PendingApproval.status == "pending")
    if conversation_id:
        stmt = stmt.where(PendingApproval.conversation_id == conversation_id)
    out = []
    for a in db.scalars(stmt.order_by(PendingApproval.created_at)):
        _expire_if_needed(db, a)
        if a.status == "pending":
            out.append(to_dict(a))
    return out
