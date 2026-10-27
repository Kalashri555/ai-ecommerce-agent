"""SQLAlchemy ORM models.

Money is stored as an integer number of paise (1 INR = 100 paise) through the
`Money` type below, and exposed to Python as `decimal.Decimal`. That keeps all
financial arithmetic exact on SQLite. On PostgreSQL you could swap the
implementation for `NUMERIC(12, 2)` without touching any business code.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.database.connection import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Money(TypeDecorator):
    """Decimal rupees in Python <-> integer paise in the database."""

    impl = Integer
    cache_ok = True

    def process_bind_param(self, value: Any, dialect) -> int | None:
        if value is None:
            return None
        amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return int(amount * 100)

    def process_result_value(self, value: Any, dialect) -> Decimal | None:
        if value is None:
            return None
        return (Decimal(int(value)) / Decimal(100)).quantize(Decimal("0.01"))


class UTCDateTime(TypeDecorator):
    """Store timezone-aware UTC datetimes; SQLite drops tzinfo otherwise."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


# ---------------------------------------------------------------------------
# Users (demo authentication)
# ---------------------------------------------------------------------------
class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
class Product(TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("stock_quantity >= 0", name="ck_products_stock_non_negative"),
        CheckConstraint("price >= 0", name="ck_products_price_non_negative"),
        Index("ix_products_category_price", "category", "price"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    brand: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    price: Mapped[Decimal] = mapped_column(Money, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    stock_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    average_rating: Mapped[float] = mapped_column(nullable=False, default=0.0)
    review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    specifications_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # Denormalised from specifications so RAM filtering is a plain indexed SQL
    # query on both SQLite and PostgreSQL.
    ram_gb: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    is_demo_data: Mapped[bool] = mapped_column(default=True, nullable=False)

    reviews: Mapped[list[ProductReview]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )

    @property
    def specifications(self) -> dict[str, Any]:
        try:
            return json.loads(self.specifications_json or "{}")
        except json.JSONDecodeError:
            return {}


class ProductReview(Base):
    __tablename__ = "product_reviews"
    __table_args__ = (CheckConstraint("rating >= 1 AND rating <= 5", name="ck_review_rating"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    review_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_sample: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    product: Mapped[Product] = relationship(back_populates="reviews")


# ---------------------------------------------------------------------------
# Cart
# ---------------------------------------------------------------------------
class ShoppingCart(TimestampMixin, Base):
    __tablename__ = "shopping_carts"
    __table_args__ = (
        # At most one *active* cart per user is enforced in cart_service; the
        # (user_id, status) index keeps that lookup fast.
        Index("ix_carts_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")

    items: Mapped[list[CartItem]] = relationship(
        back_populates="cart", cascade="all, delete-orphan", order_by="CartItem.id"
    )


class CartItem(TimestampMixin, Base):
    __tablename__ = "cart_items"
    __table_args__ = (
        UniqueConstraint("cart_id", "product_id", name="uq_cart_product"),
        CheckConstraint("quantity > 0", name="ck_cart_item_qty_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cart_id: Mapped[int] = mapped_column(ForeignKey("shopping_carts.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Money, nullable=False)  # price snapshot

    cart: Mapped[ShoppingCart] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()


# ---------------------------------------------------------------------------
# Orders (simulated checkout)
# ---------------------------------------------------------------------------
class Order(TimestampMixin, Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="simulated_confirmed")
    subtotal: Mapped[Decimal] = mapped_column(Money, nullable=False)
    total: Mapped[Decimal] = mapped_column(Money, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    # Prevents duplicate orders when the same approval / request is replayed.
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    items: Mapped[list[OrderItem]] = relationship(back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Money, nullable=False)

    order: Mapped[Order] = relationship(back_populates="items")


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------
class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="New conversation")
    # Structured memory: last filters, last products shown, etc.
    state_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    messages: Mapped[list[ConversationMessage]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="ConversationMessage.id"
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user | assistant | event
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


# ---------------------------------------------------------------------------
# Human-in-the-loop approvals
# ---------------------------------------------------------------------------
class PendingApproval(Base):
    __tablename__ = "pending_approvals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    action_arguments_json: Mapped[str] = mapped_column(Text, nullable=False)
    # SHA-256 of (user, action, canonical arguments) — the approval is only
    # valid for exactly this payload.
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    summary_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # pending -> approved -> executed | failed ;  pending -> cancelled | expired | invalidated
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    execution_result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
