"""Shopping cart and (simulated) checkout business logic.

Rules
-----
* Every function takes the *authenticated* `user_id` - callers never pass a
  cart id, so one user cannot touch another user's cart.
* Mutations run inside the caller's transaction (`session_scope`) so they
  either fully happen or not at all.
* Adding to the cart does NOT reserve stock; it validates against current
  stock. Stock is decremented only when an order is placed, using a
  conditional UPDATE (`... WHERE stock_quantity >= :qty`) so two concurrent
  checkouts can never oversell.
* Totals are always recalculated on the backend from current prices.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agent.tool_schemas import MAX_QTY_PER_ITEM
from app.database.models import CartItem, Order, OrderItem, Product, ShoppingCart
from app.services.errors import ConflictError, NotFoundError, ServiceError, ValidationFailed
from app.services.product_service import get_product_model
from app.services.serializers import money

ESTIMATE_NOTE = ("Estimated total from current demo prices. Shipping, taxes and discounts are NOT calculated "
                 "in this demo, so none are added.")


def _active_cart(db: Session, user_id: int, create: bool = True) -> ShoppingCart | None:
    cart = db.scalar(select(ShoppingCart).where(ShoppingCart.user_id == user_id, ShoppingCart.status == "active")
                     .order_by(ShoppingCart.id.desc()))
    if cart is None and create:
        cart = ShoppingCart(user_id=user_id, status="active")
        db.add(cart)
        db.flush()
    return cart


def _find_item(db: Session, cart: ShoppingCart, product: Product) -> CartItem | None:
    return db.scalar(select(CartItem).where(CartItem.cart_id == cart.id, CartItem.product_id == product.id))


def _validate_quantity(quantity: int) -> None:
    if not isinstance(quantity, int) or quantity < 1:
        raise ValidationFailed("invalid_quantity", "Quantity must be a positive whole number.")
    if quantity > MAX_QTY_PER_ITEM:
        raise ValidationFailed("quantity_limit", f"At most {MAX_QTY_PER_ITEM} units of one product per cart.",
                               details={"max_quantity": MAX_QTY_PER_ITEM})


def _ensure_stock(product: Product, quantity: int) -> None:
    if product.stock_quantity <= 0:
        raise ConflictError("out_of_stock", f"{product.name} ({product.sku}) is out of stock.",
                            details={"product_id": product.sku, "available_quantity": 0})
    if quantity > product.stock_quantity:
        raise ConflictError("insufficient_stock",
                            f"Only {product.stock_quantity} unit(s) of {product.name} are available.",
                            details={"product_id": product.sku, "available_quantity": product.stock_quantity,
                                     "requested_quantity": quantity})


def get_cart(db: Session, user_id: int) -> dict[str, Any]:
    cart = _active_cart(db, user_id, create=False)
    items, subtotal, price_changes = [], Decimal("0.00"), []
    if cart is not None:
        for it in cart.items:
            p = it.product
            line = p.price * it.quantity
            subtotal += line
            if p.price != it.unit_price:
                price_changes.append({"product_id": p.sku, "price_when_added": money(it.unit_price),
                                      "current_price": money(p.price)})
            items.append({
                "product_id": p.sku, "name": p.name, "brand": p.brand, "category": p.category,
                "quantity": it.quantity, "unit_price": money(p.price), "price_when_added": money(it.unit_price),
                "line_total": money(line), "available_quantity": p.stock_quantity,
                "stock_ok": p.stock_quantity >= it.quantity,
            })
    return {
        "ok": True,
        "items": items,
        "item_count": sum(i["quantity"] for i in items),
        "subtotal": money(subtotal),
        "currency": "INR",
        "price_changes": price_changes,
        "note": "Prices shown are current demo-catalog prices." if items else "The cart is empty.",
    }


def add_to_cart(db: Session, user_id: int, product_id: str, quantity: int = 1) -> dict[str, Any]:
    _validate_quantity(quantity)
    product = get_product_model(db, product_id)
    cart = _active_cart(db, user_id)
    item = _find_item(db, cart, product)
    new_qty = quantity + (item.quantity if item else 0)
    _validate_quantity(new_qty)
    _ensure_stock(product, new_qty)
    if item:
        item.quantity = new_qty
        item.unit_price = product.price
    else:
        db.add(CartItem(cart_id=cart.id, product_id=product.id, quantity=new_qty, unit_price=product.price))
    db.flush()
    db.refresh(cart)
    return {"ok": True, "action": "add_to_cart", "product_id": product.sku, "product_name": product.name,
            "quantity_added": quantity, "quantity_in_cart": new_qty, "unit_price": money(product.price),
            "cart": get_cart(db, user_id)}


def update_cart_item(db: Session, user_id: int, product_id: str, quantity: int) -> dict[str, Any]:
    if quantity == 0:
        raise ValidationFailed("use_remove", "Quantity 0 is not allowed here. Use remove_from_cart to remove an item.")
    _validate_quantity(quantity)
    product = get_product_model(db, product_id)
    cart = _active_cart(db, user_id, create=False)
    item = _find_item(db, cart, product) if cart else None
    if item is None:
        raise NotFoundError("not_in_cart", f"{product.name} ({product.sku}) is not in your cart.",
                            details={"product_id": product.sku})
    _ensure_stock(product, quantity)
    old = item.quantity
    item.quantity = quantity
    item.unit_price = product.price
    db.flush()
    db.refresh(cart)
    return {"ok": True, "action": "update_cart", "product_id": product.sku, "product_name": product.name,
            "previous_quantity": old, "new_quantity": quantity, "cart": get_cart(db, user_id)}


def remove_from_cart(db: Session, user_id: int, product_id: str) -> dict[str, Any]:
    product = get_product_model(db, product_id)
    cart = _active_cart(db, user_id, create=False)
    item = _find_item(db, cart, product) if cart else None
    if item is None:
        raise NotFoundError("not_in_cart", f"{product.name} ({product.sku}) is not in your cart.",
                            details={"product_id": product.sku})
    removed_qty = item.quantity
    db.delete(item)
    db.flush()
    db.refresh(cart)
    return {"ok": True, "action": "remove_from_cart", "product_id": product.sku, "product_name": product.name,
            "removed_quantity": removed_qty, "cart": get_cart(db, user_id)}


def calculate_cart_total(db: Session, user_id: int) -> dict[str, Any]:
    cart = get_cart(db, user_id)
    lines = [{"product_id": i["product_id"], "name": i["name"], "quantity": i["quantity"],
              "unit_price": i["unit_price"], "line_total": i["line_total"]} for i in cart["items"]]
    return {
        "ok": True, "lines": lines, "subtotal": cart["subtotal"],
        "discounts": [], "shipping": None, "taxes": None,
        "estimated_total": cart["subtotal"], "currency": "INR",
        "is_estimate": True, "note": ESTIMATE_NOTE, "price_changes": cart["price_changes"],
    }


def _cart_hash(user_id: int, lines: list[dict[str, Any]]) -> str:
    canonical = json.dumps({"u": user_id, "l": [(l["product_id"], l["quantity"], l["unit_price"]) for l in lines]},
                           sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def checkout_preview(db: Session, user_id: int) -> dict[str, Any]:
    """Final order summary. `cart_hash` pins exact items, quantities and prices:
    if anything changes before confirmation, the order is refused."""
    totals = calculate_cart_total(db, user_id)
    cart = get_cart(db, user_id)
    problems = [f"{i['name']}: only {i['available_quantity']} available" for i in cart["items"] if not i["stock_ok"]]
    return {
        **totals,
        "cart_hash": _cart_hash(user_id, totals["lines"]) if totals["lines"] else None,
        "can_checkout": bool(totals["lines"]) and not problems,
        "problems": problems or (["The cart is empty."] if not totals["lines"] else []),
        "simulated": True,
    }


def place_order(db: Session, user_id: int, expected_cart_hash: str | None, idempotency_key: str) -> dict[str, Any]:
    """Create a SIMULATED order. No payment is taken and no retailer is contacted."""
    # Take the write lock FIRST (a no-op UPDATE). On SQLite this makes
    # concurrent checkouts queue up instead of failing to upgrade a stale read
    # snapshot; on PostgreSQL it simply locks this user's cart row.
    db.execute(update(ShoppingCart).where(ShoppingCart.user_id == user_id, ShoppingCart.status == "active")
               .values(status="active"))
    existing =db.scalar(select(Order).where(Order.idempotency_key == idempotency_key))
    if existing is not None:
        if existing.user_id != user_id:
            raise ConflictError("idempotency_conflict", "This request id was already used.")
        return {**_order_dict(existing), "duplicate": True,
                "note": "This order was already created for this approval; no new order was placed."}

    preview = checkout_preview(db, user_id)
    if not preview["lines"]:
        raise ConflictError("empty_cart", "Your cart is empty.")
    if not expected_cart_hash:
        raise ValidationFailed("missing_cart_hash", "The order must reference an approved checkout preview.")
    if preview["cart_hash"] != expected_cart_hash:
        raise ConflictError("cart_changed",
                            "Your cart or a price changed after you reviewed the order. Please review the new summary.",
                            details={"current_summary": {k: preview[k] for k in ("lines", "subtotal", "estimated_total")}})
    if not preview["can_checkout"]:
        raise ConflictError("stock_problem", "Some items are no longer available.", details={"problems": preview["problems"]})

    cart = _active_cart(db, user_id, create=False)
    assert cart is not None
    subtotal = Decimal("0.00")
    order = Order(order_number="DEMO-" + secrets.token_hex(4).upper(), user_id=user_id,
                  status="simulated_confirmed", subtotal=Decimal("0"), total=Decimal("0"),
                  idempotency_key=idempotency_key)
    db.add(order)
    for it in cart.items:
        p = it.product
        # Atomic, race-safe stock decrement.
        res = db.execute(update(Product).where(Product.id == p.id, Product.stock_quantity >= it.quantity)
                         .values(stock_quantity=Product.stock_quantity - it.quantity))
        if res.rowcount != 1:
            raise ConflictError("insufficient_stock", f"{p.name} sold out while placing the order.",
                                details={"product_id": p.sku})
        subtotal += p.price * it.quantity
        order.items.append(OrderItem(product_id=p.id, product_name=p.name, quantity=it.quantity, unit_price=p.price))
    order.subtotal = subtotal
    order.total = subtotal
    cart.status = "ordered"
    db.flush()
    return {**_order_dict(order), "duplicate": False,
            "note": "SIMULATED order for the demo. No payment was taken and no retailer received this order."}


def _order_dict(o: Order) -> dict[str, Any]:
    return {"ok": True, "order_id": o.order_number, "status": o.status, "subtotal": money(o.subtotal),
            "total": money(o.total), "currency": o.currency, "created_at": o.created_at.isoformat() if o.created_at else None,
            "items": [{"product_name": i.product_name, "quantity": i.quantity, "unit_price": money(i.unit_price)}
                      for i in o.items]}


def list_orders(db: Session, user_id: int) -> list[dict[str, Any]]:
    orders = db.scalars(select(Order).where(Order.user_id == user_id).order_by(Order.id.desc()).limit(50)).all()
    return [_order_dict(o) for o in orders]


__all__ = ["ServiceError"]
