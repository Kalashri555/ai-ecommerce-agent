"""Turn ORM objects into plain JSON-safe dicts (money as strings)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.database.models import Product

# Specs worth showing on a product card, per category.
KEY_SPECS: dict[str, list[str]] = {
    "laptop": ["cpu", "ram_gb", "gpu", "storage_gb", "battery_hours", "display"],
    "mobile_phone": ["chipset", "ram_gb", "storage_gb", "battery_mah", "display", "camera_mp"],
    "headphones": ["type", "anc", "battery_hours", "connectivity"],
    "wireless_mouse": ["connectivity", "dpi", "battery_months", "silent"],
    "keyboard": ["layout", "switch_type", "wireless", "backlit"],
    "monitor": ["size_in", "resolution", "refresh_hz", "panel"],
    "accessories": ["type"],
}


def money(value: Decimal | None) -> str | None:
    return None if value is None else f"{value:.2f}"


def product_summary(p: Product) -> dict[str, Any]:
    specs = p.specifications
    keys = KEY_SPECS.get(p.category, [])
    return {
        "product_id": p.sku,
        "name": p.name,
        "brand": p.brand,
        "category": p.category,
        "price": money(p.price),
        "currency": p.currency,
        "average_rating": round(float(p.average_rating), 2),
        "review_count": p.review_count,
        "stock_quantity": p.stock_quantity,
        "in_stock": p.stock_quantity > 0,
        "key_specs": {k: specs[k] for k in keys if k in specs},
        "image_url": p.image_url,
        "is_demo_data": p.is_demo_data,
    }


def product_full(p: Product) -> dict[str, Any]:
    data = product_summary(p)
    data["description"] = p.description
    data["specifications"] = p.specifications
    return data
