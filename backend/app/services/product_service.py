"""Product catalog business logic (search, details, comparison).

All filtering of structured fields happens in SQL. Free-text keywords are
matched in SQL too (OR across keywords) and then ordered by how many keywords
matched. Only the requested page of results is returned to callers.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.agent.tool_schemas import CompareProductsInput, SearchProductsInput
from app.database.models import Product
from app.services.errors import NotFoundError
from app.services.serializers import product_full, product_summary

DEMO_DISCLAIMER = "Results come from the local DEMONSTRATION catalog, not live retailer listings."

# Words that carry no search meaning; category words are handled by `category`.
_STOPWORDS = {
    "a", "an", "the", "for", "with", "and", "or", "under", "below", "above", "best", "good", "cheap",
    "cheapest", "top", "find", "me", "show", "i", "need", "want", "my", "to", "of", "in", "on", "rs",
    "inr", "price", "budget", "buy", "some", "any", "please", "product", "products", "programming",
}


def _keywords(query: str | None) -> list[str]:
    if not query:
        return []
    words = re.findall(r"[a-z0-9]+", query.lower())
    return [w for w in words if len(w) >= 2 and w not in _STOPWORDS and not w.isdigit()][:8]


def _get_by_sku(db: Session, product_id: str) -> Product:
    product = db.scalar(select(Product).where(Product.sku == product_id))
    if product is None:
        raise NotFoundError("product_not_found", f"Product {product_id} was not found in the catalog.",
                            details={"product_id": product_id})
    return product


def get_product_model(db: Session, product_id: str) -> Product:
    return _get_by_sku(db, product_id)


def search_products(db: Session, params: SearchProductsInput, offset: int = 0) -> dict[str, Any]:
    stmt = select(Product)
    if params.category:
        stmt = stmt.where(Product.category == params.category)
    if params.min_price is not None:
        stmt = stmt.where(Product.price >= Decimal(str(params.min_price)))
    if params.max_price is not None:
        stmt = stmt.where(Product.price <= Decimal(str(params.max_price)))
    if params.min_rating is not None:
        stmt = stmt.where(Product.average_rating >= params.min_rating)
    if params.min_ram_gb is not None:
        stmt = stmt.where(Product.ram_gb >= params.min_ram_gb)
    if params.brand:
        stmt = stmt.where(func.lower(Product.brand) == params.brand.lower())
    if params.in_stock_only:
        stmt = stmt.where(Product.stock_quantity > 0)

    words = _keywords(params.query)
    if words:
        clauses = []
        for w in words:
            like = f"%{w}%"
            clauses.append(or_(func.lower(Product.name).like(like), func.lower(Product.brand).like(like),
                               func.lower(Product.category).like(like),
                               func.lower(Product.specifications_json).like(like)))
        stmt = stmt.where(or_(*clauses))

    rows = list(db.scalars(stmt))

    def relevance(p: Product) -> int:
        hay = f"{p.name} {p.brand} {p.category} {p.specifications_json}".lower()
        return sum(1 for w in words if w in hay)

    if params.sort_by == "price_asc":
        rows.sort(key=lambda p: (p.price, -p.average_rating, p.sku))
    elif params.sort_by == "price_desc":
        rows.sort(key=lambda p: (-p.price, -p.average_rating, p.sku))
    elif params.sort_by == "rating_desc":
        rows.sort(key=lambda p: (-p.average_rating, -p.review_count, p.sku))
    else:
        rows.sort(key=lambda p: (-relevance(p), -p.average_rating, p.price, p.sku))

    total = len(rows)
    page = rows[offset: offset + params.limit]
    filters = params.model_dump(exclude_none=True, exclude_defaults=False)
    result: dict[str, Any] = {
        "ok": True,
        "total_matches": total,
        "returned": len(page),
        "offset": offset,
        "filters_applied": filters,
        "products": [product_summary(p) for p in page],
        "data_source": DEMO_DISCLAIMER,
    }
    if total == 0:
        result["message"] = "No products match these filters. Consider relaxing the budget, rating or RAM constraint."
    return result


def get_product_details(db: Session, product_id: str, review_limit: int = 3) -> dict[str, Any]:
    from app.services.review_service import reviews_for

    product = _get_by_sku(db, product_id)
    data = product_full(product)
    data["sample_reviews"] = reviews_for(db, product, review_limit)
    return {"ok": True, "product": data, "data_source": DEMO_DISCLAIMER}


def compare_products(db: Session, params: CompareProductsInput) -> dict[str, Any]:
    from app.agent.scoring_service import ScoringCriteria, product_to_scoring_data, score_products

    products = db.scalars(select(Product).where(Product.sku.in_(params.product_ids))).all()
    found = {p.sku: p for p in products}
    missing = [pid for pid in params.product_ids if pid not in found]
    if missing:
        raise NotFoundError("product_not_found", f"Product(s) not found: {', '.join(missing)}",
                            details={"missing_product_ids": missing})
    ordered = [found[pid] for pid in params.product_ids]

    scoring: dict[str, Any] | None = None
    categories = {p.category for p in ordered}
    if len(categories) == 1:
        criteria = ScoringCriteria(category=ordered[0].category, budget=params.budget, use_case=params.use_case,
                                   in_stock_only=False, top_n=len(ordered))
        ranking = score_products([product_to_scoring_data(p) for p in ordered], criteria)
        scoring = {r["product_id"]: r for r in ranking["ranked"]}

    rows = []
    for p in ordered:
        row = product_full(p)
        row.pop("description", None)
        if scoring and p.sku in scoring:
            s = scoring[p.sku]
            row["score"] = {k: s[k] for k in ("total_score", "rank", "components", "warnings", "explanation")}
        rows.append(row)
    return {
        "ok": True,
        "products": rows,
        "scoring_note": None if scoring else "Products span several categories, so no common score was computed.",
        "weights": ranking["weights"] if scoring else None,
        "data_source": DEMO_DISCLAIMER,
    }


def rank_products(db, params) -> dict[str, Any]:
    """Fetch candidates for a category from SQL, then run the deterministic scorer.

    Candidates are fetched by category (plus explicit product_ids, if given)
    WITHOUT the other filters, so the scorer can report *why* products were
    excluded by hard constraints. The catalog per category is small; with a
    large catalog you would pre-filter in SQL and only report counts.
    """
    from app.agent.scoring_service import ScoringCriteria, product_to_scoring_data, score_products

    stmt = select(Product).where(Product.category == params.category)
    if params.product_ids:
        stmt = select(Product).where(Product.sku.in_(params.product_ids))
        found = set(db.scalars(select(Product.sku).where(Product.sku.in_(params.product_ids))))
        missing = [p for p in params.product_ids if p not in found]
        if missing:
            raise NotFoundError("product_not_found", f"Product(s) not found: {', '.join(missing)}",
                                details={"missing_product_ids": missing})
    candidates = [product_to_scoring_data(p) for p in db.scalars(stmt.limit(500))]
    criteria = ScoringCriteria(
        category=params.category, budget=params.max_price, min_ram_gb=params.min_ram_gb,
        min_rating=params.min_rating, brand=params.brand, required_features=params.required_features,
        in_stock_only=params.in_stock_only, use_case=params.use_case,
        weights=params.weights.model_dump() if params.weights else None, top_n=params.top_n,
    )
    result = score_products(candidates, criteria)
    result["data_source"] = DEMO_DISCLAIMER
    return result
