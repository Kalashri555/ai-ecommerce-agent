"""Product catalog REST endpoints (used by the UI directly; the agent uses MCP)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.agent.tool_schemas import CompareProductsInput, ProductIdInput, SearchProductsInput
from app.database.connection import get_db
from app.services import product_service, review_service
from app.services.errors import ValidationFailed

router = APIRouter(prefix="/api/products", tags=["products"])


def _validate(model, **data):
    try:
        return model(**{k: v for k, v in data.items() if v is not None})
    except ValidationError as exc:
        raise ValidationFailed("invalid_parameters", "Invalid query parameters.",
                               details={"issues": [{"field": ".".join(map(str, e["loc"])), "message": e["msg"]}
                                                   for e in exc.errors()]})


@router.get("")
def list_products(
    q: str | None = None, category: str | None = None, min_price: float | None = None,
    max_price: float | None = None, min_rating: float | None = None, min_ram_gb: int | None = None,
    brand: str | None = None, in_stock_only: bool = False, sort_by: str = "relevance",
    limit: int = Query(12, ge=1, le=20), offset: int = Query(0, ge=0, le=10000), db: Session = Depends(get_db),
):
    params = _validate(SearchProductsInput, query=q, category=category, min_price=min_price, max_price=max_price,
                       min_rating=min_rating, min_ram_gb=min_ram_gb, brand=brand, in_stock_only=in_stock_only,
                       sort_by=sort_by, limit=limit)
    return product_service.search_products(db, params, offset=offset)


@router.get("/compare")
def compare(ids: str = Query(..., description="Comma-separated product IDs"), budget: float | None = None,
            use_case: str = "general", db: Session = Depends(get_db)):
    params = _validate(CompareProductsInput, product_ids=[i for i in ids.split(",") if i.strip()],
                       budget=budget, use_case=use_case)
    return product_service.compare_products(db, params)


@router.get("/{product_id}")
def product_detail(product_id: str, db: Session = Depends(get_db)):
    p = _validate(ProductIdInput, product_id=product_id)
    return product_service.get_product_details(db, p.product_id)


@router.get("/{product_id}/reviews")
def product_reviews(product_id: str, limit: int = Query(5, ge=1, le=20), db: Session = Depends(get_db)):
    p = _validate(ProductIdInput, product_id=product_id)
    return review_service.get_product_reviews(db, p.product_id, limit)
