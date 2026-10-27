"""Cart and checkout business rules."""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.database.connection import SessionLocal, session_scope
from app.database.models import Product
from app.services import cart_service
from app.services.errors import ConflictError, NotFoundError, ValidationFailed
from app.tests.conftest import login

DEMO, ALICE = 1, 2


def test_add_update_remove(fresh_db):
    with session_scope() as db:
        r = cart_service.add_to_cart(db, DEMO, "MOU-001", 2)
        assert r["quantity_in_cart"] == 2 and r["cart"]["subtotal"] == "1798.00"
        r = cart_service.add_to_cart(db, DEMO, "MOU-001", 1)  # merges into the same line
        assert r["quantity_in_cart"] == 3 and len(r["cart"]["items"]) == 1
        r = cart_service.update_cart_item(db, DEMO, "MOU-001", 1)
        assert r["previous_quantity"] == 3 and r["new_quantity"] == 1
        r = cart_service.remove_from_cart(db, DEMO, "MOU-001")
        assert r["cart"]["items"] == []
        with pytest.raises(NotFoundError):
            cart_service.remove_from_cart(db, DEMO, "MOU-001")


def test_insufficient_and_out_of_stock(fresh_db):
    with session_scope() as db:
        with pytest.raises(ConflictError) as e:
            cart_service.add_to_cart(db, DEMO, "LAP-010", 1)
        assert e.value.code == "out_of_stock"
        with pytest.raises(ConflictError) as e:
            cart_service.add_to_cart(db, DEMO, "LAP-015", 5)  # stock 4
        assert e.value.code == "insufficient_stock"


def test_invalid_quantities(fresh_db):
    with session_scope() as db:
        for q in (0, -1, 11):
            with pytest.raises(ValidationFailed):
                cart_service.add_to_cart(db, DEMO, "MOU-001", q)
        cart_service.add_to_cart(db, DEMO, "MOU-001", 1)
        with pytest.raises(ValidationFailed) as e:
            cart_service.update_cart_item(db, DEMO, "MOU-001", 0)
        assert e.value.code == "use_remove"


def test_cart_isolation_between_users(fresh_db):
    with session_scope() as db:
        cart_service.add_to_cart(db, DEMO, "MOU-001", 1)
        assert cart_service.get_cart(db, ALICE)["items"] == []
        with pytest.raises(NotFoundError):
            cart_service.remove_from_cart(db, ALICE, "MOU-001")  # cannot touch demo's item
        assert len(cart_service.get_cart(db, DEMO)["items"]) == 1


def test_price_recalculation_and_cart_hash(fresh_db):
    with session_scope() as db:
        cart_service.add_to_cart(db, DEMO, "MOU-003", 2)
        preview = cart_service.checkout_preview(db, DEMO)
        assert preview["estimated_total"] == "2590.00" and preview["shipping"] is None and preview["taxes"] is None
        db.scalar(select(Product).where(Product.sku == "MOU-003")).price = Decimal("1395.00")
    with session_scope() as db:
        cart = cart_service.get_cart(db, DEMO)
        assert cart["subtotal"] == "2790.00"  # current price, not the snapshot
        assert cart["price_changes"][0]["price_when_added"] == "1295.00"
        with pytest.raises(ConflictError) as e:
            cart_service.place_order(db, DEMO, preview["cart_hash"], "idem-1")
        assert e.value.code == "cart_changed"


def test_place_order_idempotent_and_decrements_stock(fresh_db):
    with session_scope() as db:
        cart_service.add_to_cart(db, DEMO, "LAP-015", 2)
        h = cart_service.checkout_preview(db, DEMO)["cart_hash"]
        o1 = cart_service.place_order(db, DEMO, h, "idem-x")
        assert o1["status"] == "simulated_confirmed" and o1["total"] == "299980.00" and not o1["duplicate"]
    with session_scope() as db:
        o2 = cart_service.place_order(db, DEMO, h, "idem-x")
        assert o2["duplicate"] and o2["order_id"] == o1["order_id"]
        assert db.scalar(select(Product).where(Product.sku == "LAP-015")).stock_quantity == 2
        assert cart_service.get_cart(db, DEMO)["items"] == []  # cart closed after order
        assert len(cart_service.list_orders(db, DEMO)) == 1


def test_concurrent_checkout_cannot_oversell(fresh_db):
    """Two users race for the last unit; the conditional UPDATE lets only one win."""
    with session_scope() as db:
        db.scalar(select(Product).where(Product.sku == "MON-003")).stock_quantity = 1
    hashes = {}
    with session_scope() as db:
        for uid in (DEMO, ALICE):
            cart_service.add_to_cart(db, uid, "MON-003", 1)
            hashes[uid] = cart_service.checkout_preview(db, uid)["cart_hash"]

    results: dict[int, str] = {}
    barrier = threading.Barrier(2)

    def buy(uid: int) -> None:
        barrier.wait()
        db = SessionLocal()
        try:
            cart_service.place_order(db, uid, hashes[uid], f"race-{uid}")
            db.commit()
            results[uid] = "ok"
        except ConflictError as exc:
            db.rollback()
            results[uid] = exc.code
        finally:
            db.close()

    threads = [threading.Thread(target=buy, args=(u,)) for u in (DEMO, ALICE)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results.values()) in (["insufficient_stock", "ok"], ["ok", "stock_problem"])
    with session_scope() as db:
        assert db.scalar(select(Product).where(Product.sku == "MON-003")).stock_quantity == 0


def test_rest_cart_requires_auth_and_is_isolated(client):
    assert client.get("/api/cart").status_code == 401
    demo, bob = login(client, "demo"), login(client, "bob")
    assert client.post("/api/cart/items", json={"product_id": "MOU-001", "quantity": 1}, headers=demo).status_code == 201
    assert client.get("/api/cart", headers=bob).json()["items"] == []
    assert client.delete("/api/cart/items/MOU-001", headers=bob).status_code == 404
    assert client.patch("/api/cart/items/MOU-001", json={"quantity": 3}, headers=demo).json()["new_quantity"] == 3
    assert client.post("/api/cart/items", json={"product_id": "MOU-001", "quantity": 0}, headers=demo).status_code == 422
    bad = client.get("/api/cart", headers={"Authorization": "Bearer forged.token"})
    assert bad.status_code == 401
