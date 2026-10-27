"""Cart, checkout and order endpoints.

The cart endpoints here are for actions the USER performs directly in the UI
(e.g. clicking "-" in the cart panel) - an explicit user action is its own
confirmation. Cart changes the AI agent wants to make always go through the
approval workflow and the Shopping Cart MCP server instead.

Checkout always requires an approval: preview creates it, confirm executes it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.agent import approval_service
from app.api.chat import _approve_and_execute
from app.api.deps import get_agent, get_executor
from app.auth.dependencies import CurrentUser, get_current_user
from app.database.connection import get_db, session_scope
from app.database.schemas import ApprovalDecisionResponse, CartItemCreate, CartItemUpdate, CheckoutConfirmRequest
from app.agent.tool_schemas import normalize_product_id
from app.services import cart_service
from app.services.errors import ServiceError, ValidationFailed

router = APIRouter(prefix="/api", tags=["cart"])


def _pid(product_id: str) -> str:
    try:
        return normalize_product_id(product_id)
    except ValueError as exc:
        raise ValidationFailed("invalid_product_id", str(exc))


@router.get("/cart")
def get_cart(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return cart_service.get_cart(db, user.id)


@router.post("/cart/items", status_code=201)
def add_item(body: CartItemCreate, user: CurrentUser = Depends(get_current_user)):
    with session_scope() as db:
        return cart_service.add_to_cart(db, user.id, body.product_id, body.quantity)


@router.patch("/cart/items/{product_id}")
def update_item(product_id: str, body: CartItemUpdate, user: CurrentUser = Depends(get_current_user)):
    with session_scope() as db:
        return cart_service.update_cart_item(db, user.id, _pid(product_id), body.quantity)


@router.delete("/cart/items/{product_id}")
def remove_item(product_id: str, user: CurrentUser = Depends(get_current_user)):
    with session_scope() as db:
        return cart_service.remove_from_cart(db, user.id, _pid(product_id))


@router.get("/cart/total")
def cart_total(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return cart_service.calculate_cart_total(db, user.id)


@router.post("/checkout/preview")
async def checkout_preview(user: CurrentUser = Depends(get_current_user), executor=Depends(get_executor)):
    """Build the final order summary and create a pending approval for it."""
    prepared = await executor.prepare_action("place_order", {}, user.id)
    if not prepared["ok"]:
        raise ServiceError(prepared["error"]["code"], prepared["error"]["message"], http_status=409)
    with session_scope() as db:
        a = approval_service.create_approval(db, user_id=user.id, action_type="place_order",
                                             arguments=prepared["arguments"], summary=prepared["summary"])
        return {"approval": approval_service.to_dict(a)}


@router.post("/checkout/confirm", response_model=ApprovalDecisionResponse)
async def checkout_confirm(body: CheckoutConfirmRequest, user: CurrentUser = Depends(get_current_user),
                           executor=Depends(get_executor), agent=Depends(get_agent)):
    with session_scope() as db:
        a = approval_service.get_owned(db, body.approval_id, user.id)
        if a.action_type != "place_order":
            raise ValidationFailed("wrong_approval", "This approval is not for checkout.")
    return await _approve_and_execute(body.approval_id, user, executor, agent, continue_agent=False)


@router.get("/orders")
def orders(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return {"orders": cart_service.list_orders(db, user.id)}
