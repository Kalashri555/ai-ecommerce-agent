"""Product reviews. Only real rows from the local sample database are returned;
nothing is generated at runtime."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Product, ProductReview


def reviews_for(db: Session, product: Product, limit: int = 5) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(ProductReview).where(ProductReview.product_id == product.id)
        .order_by(ProductReview.created_at.desc(), ProductReview.id).limit(limit)
    ).all()
    return [{"rating": r.rating, "review_text": r.review_text, "is_sample": r.is_sample,
             "created_at": r.created_at.isoformat()} for r in rows]


def get_product_reviews(db: Session, product_id: str, limit: int = 5) -> dict[str, Any]:
    from app.services.product_service import get_product_model

    product = get_product_model(db, product_id)
    reviews = reviews_for(db, product, limit)
    return {
        "ok": True,
        "product_id": product.sku,
        "product_name": product.name,
        "average_rating": round(float(product.average_rating), 2),
        "review_count": product.review_count,
        "reviews": reviews,
        "note": ("Reviews are SAMPLE demonstration data. review_count is the demo aggregate; "
                 "only a few representative reviews are stored."),
    }
