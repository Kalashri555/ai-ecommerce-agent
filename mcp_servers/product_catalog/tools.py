"""Product Catalog tool implementations (thin adapters over shared services)."""

from __future__ import annotations

from typing import Any

from app.agent import tool_schemas as ts
from app.services import inventory_service, product_service, review_service


def search_products(db, p: ts.SearchProductsInput) -> dict[str, Any]:
    return product_service.search_products(db, p)


def get_product_details(db, p: ts.ProductIdInput) -> dict[str, Any]:
    return product_service.get_product_details(db, p.product_id)


def check_inventory(db, p: ts.CheckInventoryInput) -> dict[str, Any]:
    return inventory_service.check_inventory(db, p.product_id, p.requested_quantity)


def get_product_reviews(db, p: ts.GetReviewsInput) -> dict[str, Any]:
    return review_service.get_product_reviews(db, p.product_id, p.limit)


def compare_products(db, p: ts.CompareProductsInput) -> dict[str, Any]:
    return product_service.compare_products(db, p)


def rank_products(db, p: ts.RankProductsInput) -> dict[str, Any]:
    from app.agent.scoring_service import ScoringError
    from app.services.errors import ValidationFailed

    try:
        return product_service.rank_products(db, p)
    except ScoringError as exc:
        raise ValidationFailed("invalid_scoring_criteria", str(exc))
