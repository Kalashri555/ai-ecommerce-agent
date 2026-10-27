"""Product catalog: service-level and REST tests."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.agent.tool_schemas import CompareProductsInput, SearchProductsInput
from app.database.connection import session_scope
from app.services import inventory_service, product_service, review_service
from app.services.errors import NotFoundError


def search(**kw):
    with session_scope() as db:
        return product_service.search_products(db, SearchProductsInput(**kw))


def test_catalog_has_at_least_50_demo_products():
    r = search(limit=20)
    assert r["total_matches"] >= 50
    assert all(p["is_demo_data"] for p in r["products"])
    assert "DEMONSTRATION" in r["data_source"]


def test_price_filter():
    r = search(category="wireless_mouse", max_price=2000, limit=20)
    assert r["total_matches"] > 0
    assert all(Decimal(p["price"]) <= 2000 for p in r["products"])
    r = search(category="laptop", min_price=60000, max_price=80000, limit=20)
    assert all(60000 <= Decimal(p["price"]) <= 80000 for p in r["products"])


def test_category_filter_and_aliases():
    r = search(category="mice", limit=20)
    assert {p["category"] for p in r["products"]} == {"wireless_mouse"}
    r = search(category="Laptops", limit=20)
    assert {p["category"] for p in r["products"]} == {"laptop"}


def test_ram_filter():
    r = search(category="laptop", min_ram_gb=16, limit=20)
    assert r["total_matches"] > 0
    assert all(p["key_specs"]["ram_gb"] >= 16 for p in r["products"])


def test_rating_filter():
    r = search(min_rating=4.5, limit=20)
    assert all(p["average_rating"] >= 4.5 for p in r["products"])


def test_inventory_filter():
    all_mice = search(category="wireless_mouse", limit=20)
    in_stock = search(category="wireless_mouse", in_stock_only=True, limit=20)
    assert any(not p["in_stock"] for p in all_mice["products"])
    assert all(p["in_stock"] for p in in_stock["products"])
    assert in_stock["total_matches"] < all_mice["total_matches"]


def test_sort_by_price_asc():
    r = search(category="wireless_mouse", sort_by="price_asc", limit=20)
    prices = [Decimal(p["price"]) for p in r["products"]]
    assert prices == sorted(prices)


def test_keyword_query():
    r = search(query="RTX", category="laptop", limit=20)
    assert r["total_matches"] >= 3
    assert all("RTX" in p["key_specs"].get("gpu", "") for p in r["products"])


def test_unrealistic_constraints_return_explicit_no_match():
    r = search(category="laptop", max_price=10000, min_ram_gb=64)
    assert r["total_matches"] == 0 and r["products"] == []
    assert "No products match" in r["message"]


def test_invalid_parameters_rejected():
    with pytest.raises(ValidationError):
        SearchProductsInput(min_price=5000, max_price=1000)
    with pytest.raises(ValidationError):
        SearchProductsInput(limit=500)
    with pytest.raises(ValidationError):
        SearchProductsInput(category="spaceships")
    with pytest.raises(ValidationError):
        SearchProductsInput(user_id=5)  # extra fields forbidden - no identity via tool args


def test_product_details_and_missing_product():
    with session_scope() as db:
        d = product_service.get_product_details(db, "LAP-001")["product"]
        assert d["product_id"] == "LAP-001" and d["specifications"]["ram_gb"] == 16
        assert d["sample_reviews"] and all(r["is_sample"] for r in d["sample_reviews"])
        with pytest.raises(NotFoundError):
            product_service.get_product_details(db, "LAP-999")


def test_inventory_and_reviews():
    with session_scope() as db:
        inv = inventory_service.check_inventory(db, "LAP-010", 1)
        assert inv["in_stock"] is False and inv["requested_quantity_available"] is False
        rev = review_service.get_product_reviews(db, "MOU-001", 2)
        assert len(rev["reviews"]) == 2


def test_compare_products_includes_scores_and_validates_ids():
    with session_scope() as db:
        r = product_service.compare_products(db, CompareProductsInput(product_ids=["LAP-001", "LAP-002", "LAP-003"], budget=80000))
        assert [p["product_id"] for p in r["products"]] == ["LAP-001", "LAP-002", "LAP-003"]
        assert all("score" in p for p in r["products"])
        with pytest.raises(NotFoundError):
            product_service.compare_products(db, CompareProductsInput(product_ids=["LAP-001", "LAP-998"]))
    with pytest.raises(ValidationError):
        CompareProductsInput(product_ids=["LAP-001"])  # need at least two


# --------------------------------------------------------------------- REST
def test_rest_search_pagination(client):
    p1 = client.get("/api/products", params={"category": "laptop", "limit": 5, "offset": 0, "sort_by": "price_asc"}).json()
    p2 = client.get("/api/products", params={"category": "laptop", "limit": 5, "offset": 5, "sort_by": "price_asc"}).json()
    assert p1["total_matches"] == p2["total_matches"] >= 10
    ids1 = {p["product_id"] for p in p1["products"]}
    ids2 = {p["product_id"] for p in p2["products"]}
    assert ids1.isdisjoint(ids2) and len(ids1) == 5


def test_rest_errors(client):
    assert client.get("/api/products/LAP-999").status_code == 404
    r = client.get("/api/products/not-an-id")
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_parameters"
    assert client.get("/api/products", params={"min_price": 100, "max_price": 10}).status_code == 422
    r = client.get("/api/products/compare", params={"ids": "LAP-001,LAP-002", "budget": 80000})
    assert r.status_code == 200 and len(r.json()["products"]) == 2
    assert client.get("/api/products/MOU-001/reviews").json()["reviews"]
