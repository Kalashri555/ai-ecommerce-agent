"""Pydantic input models for every agent tool.

These models are the single source of truth for tool-argument validation:

* the MCP servers validate incoming arguments with them, and
* the backend validates the model's arguments with them *before* it forwards a
  call to an MCP server (so a bad call never leaves the backend).

The JSON schema the LLM sees is *discovered from the MCP servers* at runtime
(see mcp_client.py); a test checks that it stays consistent with these models.

Note: there is deliberately no `user_id` field anywhere. Identity comes from
the authenticated request context, never from the model.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CATEGORIES = ("laptop", "mobile_phone", "headphones", "wireless_mouse", "keyboard", "monitor", "accessories")
Category = Literal["laptop", "mobile_phone", "headphones", "wireless_mouse", "keyboard", "monitor", "accessories"]

# Friendly spellings the model (or a user) might use -> canonical category.
CATEGORY_ALIASES = {
    "laptops": "laptop", "notebook": "laptop", "notebooks": "laptop",
    "phone": "mobile_phone", "phones": "mobile_phone", "mobile": "mobile_phone", "mobiles": "mobile_phone",
    "mobile phone": "mobile_phone", "mobile phones": "mobile_phone", "smartphone": "mobile_phone", "smartphones": "mobile_phone",
    "headphone": "headphones", "earbuds": "headphones", "earphones": "headphones", "headset": "headphones",
    "mouse": "wireless_mouse", "mice": "wireless_mouse", "wireless mouse": "wireless_mouse", "wireless mice": "wireless_mouse",
    "keyboards": "keyboard", "monitors": "monitor", "display": "monitor", "displays": "monitor",
    "accessory": "accessories",
}

PRODUCT_ID_RE = re.compile(r"^[A-Z]{3}-\d{3}$")
MAX_SEARCH_LIMIT = 20
MAX_QTY_PER_ITEM = 10

UseCase = Literal["general", "basic_programming", "office", "ml_cpu", "ml_gpu", "gaming"]
FEATURE_FLAGS = ("anc", "wireless", "gpu_cuda", "five_g", "backlit", "silent", "rechargeable", "microphone")


def normalize_category(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    v = value.strip().lower().replace("-", " ").replace("_", " ")
    if v.replace(" ", "_") in CATEGORIES:
        return v.replace(" ", "_")
    if v in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[v]
    raise ValueError(f"Unknown category '{value}'. Valid categories: {', '.join(CATEGORIES)}")


def normalize_product_id(value: str) -> str:
    v = (value or "").strip().upper()
    if not PRODUCT_ID_RE.match(v):
        raise ValueError("product_id must look like 'LAP-001' (three letters, dash, three digits)")
    return v


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _CategoryMixin(_Base):
    @field_validator("category", mode="before", check_fields=False)
    @classmethod
    def _cat(cls, v):
        return normalize_category(v) if isinstance(v, str) else v


# ------------------------------------------------------------------ catalog
class SearchProductsInput(_CategoryMixin):
    query: str | None = Field(None, max_length=200, description="Free-text keywords, e.g. 'silent' or 'RTX'.")
    category: Category | None = None
    min_price: float | None = Field(None, ge=0)
    max_price: float | None = Field(None, gt=0)
    min_rating: float | None = Field(None, ge=0, le=5)
    min_ram_gb: int | None = Field(None, ge=1, le=256)
    brand: str | None = Field(None, max_length=80)
    in_stock_only: bool = False
    sort_by: Literal["relevance", "price_asc", "price_desc", "rating_desc"] = "relevance"
    limit: int = Field(10, ge=1, le=MAX_SEARCH_LIMIT)

    @model_validator(mode="after")
    def _price_range(self):
        if self.min_price is not None and self.max_price is not None and self.min_price > self.max_price:
            raise ValueError("min_price cannot be greater than max_price")
        return self


class ProductIdInput(_Base):
    product_id: str

    @field_validator("product_id")
    @classmethod
    def _pid(cls, v: str) -> str:
        return normalize_product_id(v)


class CheckInventoryInput(ProductIdInput):
    requested_quantity: int | None = Field(None, ge=1, le=1000)


class GetReviewsInput(ProductIdInput):
    limit: int = Field(5, ge=1, le=20)


class CompareProductsInput(_Base):
    product_ids: list[str] = Field(..., min_length=2, max_length=6)
    budget: float | None = Field(None, gt=0, description="Optional budget used for the price-value score.")
    use_case: UseCase = "general"

    @field_validator("product_ids")
    @classmethod
    def _pids(cls, v: list[str]) -> list[str]:
        ids = [normalize_product_id(x) for x in v]
        if len(set(ids)) != len(ids):
            raise ValueError("product_ids must be unique")
        return ids


class ScoringWeights(_Base):
    performance: float = Field(0, ge=0)
    price_value: float = Field(0, ge=0)
    rating: float = Field(0, ge=0)
    battery: float = Field(0, ge=0)


class RankProductsInput(_CategoryMixin):
    category: Category
    max_price: float | None = Field(None, gt=0, description="Budget; a hard constraint and the price-value reference.")
    min_ram_gb: int | None = Field(None, ge=1, le=256)
    min_rating: float | None = Field(None, ge=0, le=5)
    brand: str | None = Field(None, max_length=80)
    required_features: list[str] = Field(default_factory=list, max_length=5)
    in_stock_only: bool = True
    use_case: UseCase = "general"
    weights: ScoringWeights | None = None
    product_ids: list[str] | None = Field(None, max_length=20, description="Optionally rank only these products.")
    top_n: int = Field(5, ge=1, le=20)

    @field_validator("required_features")
    @classmethod
    def _features(cls, v: list[str]) -> list[str]:
        out = []
        for f in v:
            f = f.strip().lower()
            if f not in FEATURE_FLAGS:
                raise ValueError(f"Unknown feature '{f}'. Supported: {', '.join(FEATURE_FLAGS)}")
            out.append(f)
        return out

    @field_validator("product_ids")
    @classmethod
    def _pids(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else [normalize_product_id(x) for x in v]


# --------------------------------------------------------------------- cart
class EmptyInput(_Base):
    pass


class AddToCartInput(ProductIdInput):
    quantity: int = Field(1, ge=1, le=MAX_QTY_PER_ITEM)


class UpdateCartInput(ProductIdInput):
    quantity: int = Field(..., ge=1, le=MAX_QTY_PER_ITEM,
                          description="New quantity (1-10). To remove an item use remove_from_cart.")


class RemoveFromCartInput(ProductIdInput):
    pass


class PlaceOrderInput(_Base):
    # Filled in by the backend from the checkout preview the user approved;
    # the model normally calls place_order with no arguments.
    expected_cart_hash: str | None = Field(None, max_length=64)


TOOL_INPUT_MODELS: dict[str, type[BaseModel]] = {
    "search_products": SearchProductsInput,
    "get_product_details": ProductIdInput,
    "check_inventory": CheckInventoryInput,
    "get_product_reviews": GetReviewsInput,
    "compare_products": CompareProductsInput,
    "rank_products": RankProductsInput,
    "get_cart": EmptyInput,
    "add_to_cart": AddToCartInput,
    "update_cart": UpdateCartInput,
    "remove_from_cart": RemoveFromCartInput,
    "calculate_cart_total": EmptyInput,
    "checkout_preview": EmptyInput,
    "place_order": PlaceOrderInput,
}
