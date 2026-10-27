"""Inventory checks."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.services.product_service import get_product_model


def check_inventory(db: Session, product_id: str, requested_quantity: int | None = None) -> dict[str, Any]:
    product = get_product_model(db, product_id)
    available = product.stock_quantity
    result: dict[str, Any] = {
        "ok": True,
        "product_id": product.sku,
        "product_name": product.name,
        "available_quantity": available,
        "in_stock": available > 0,
    }
    if requested_quantity is not None:
        result["requested_quantity"] = requested_quantity
        result["requested_quantity_available"] = available >= requested_quantity
    return result
