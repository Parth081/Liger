"""Order-taking models (DATA_MODEL §5): cart, quotations, orders with frozen
snapshots (BR-SQFT-10, R9), append-only status history (BR-ORD-03)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import ActorMixin, Base, PKMixin, TimestampMixin, VersionMixin

_FK_INT = BigInteger().with_variant(Integer, "sqlite")
_JSON = JSON().with_variant(JSONB, "postgresql")

ORDER_STATUS = sa.Enum(
    "DRAFT", "PENDING_APPROVAL", "CONFIRMED", "IN_PRODUCTION", "READY",
    "DISPATCHED", "PARTIALLY_DELIVERED", "DELIVERED", "CLOSED",
    "CANCELLED", "ON_HOLD_CREDIT",
    name="order_status",
)  # BR-ORD-01


class Cart(Base, PKMixin, TimestampMixin):
    """Owner-bound — never anonymous (P2-T1-01)."""

    __tablename__ = "carts"
    __table_args__ = (Index("uq_cart_owner", "owner_type", "owner_id", unique=True),)

    owner_type: Mapped[str] = mapped_column(String(20))  # customer_user | user
    owner_id: Mapped[int] = mapped_column(_FK_INT)
    customer_id: Mapped[int | None] = mapped_column(_FK_INT, ForeignKey("customers.id"),
                                                    nullable=True, index=True)

    items: Mapped[list[CartItem]] = relationship(back_populates="cart", lazy="selectin",
                                                 cascade="all, delete-orphan")


class CartItem(Base, PKMixin, TimestampMixin):
    __tablename__ = "cart_items"

    cart_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("carts.id"), index=True)
    design_no: Mapped[str] = mapped_column(String(50))
    length_in: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    breadth_in: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    quantity: Mapped[int] = mapped_column(Integer)
    room_label: Mapped[str | None] = mapped_column(String(60), nullable=True)  # BR-ORD-10
    line_discount_paise: Mapped[int] = mapped_column(BigInteger, default=0)

    cart: Mapped[Cart] = relationship(back_populates="items")


class Order(Base, PKMixin, TimestampMixin, ActorMixin, VersionMixin):
    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_customer_date", "customer_id", "order_date"),
        Index("ix_orders_status", "status"),
    )

    order_no: Mapped[str] = mapped_column(String(30), unique=True, index=True)  # BR-ORD-06
    customer_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("customers.id"), index=True)
    placed_by_type: Mapped[str] = mapped_column(String(20))     # BR-ORD-08
    placed_by_id: Mapped[int] = mapped_column(_FK_INT)
    channel: Mapped[str] = mapped_column(String(10), default="web")  # web|app|staff
    status: Mapped[str] = mapped_column(ORDER_STATUS, default="CONFIRMED")
    order_date: Mapped[date] = mapped_column(Date)
    expected_delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Money (R1) — all paise
    subtotal_paise: Mapped[int] = mapped_column(BigInteger)
    order_discount_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    taxable_paise: Mapped[int] = mapped_column(BigInteger)
    cgst_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    sgst_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    igst_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    freight_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    packing_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    round_off_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    grand_total_paise: Mapped[int] = mapped_column(BigInteger)

    credit_decision: Mapped[dict[str, Any] | None] = mapped_column(_JSON, nullable=True)  # BR-CR-21
    rate_card_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)  # R6
    site_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    remarks: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_prepaid: Mapped[bool] = mapped_column(Boolean, default=False)  # BR-CR-12

    items: Mapped[list[OrderItem]] = relationship(back_populates="order", lazy="selectin")
    status_history: Mapped[list[OrderStatusHistory]] = relationship(
        back_populates="order", lazy="selectin", order_by="OrderStatusHistory.id"
    )


class OrderItem(Base, PKMixin):
    """Frozen snapshot — NEVER recalculated after creation (R9, BR-SQFT-10)."""

    __tablename__ = "order_items"

    order_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("orders.id"), index=True)
    design_id: Mapped[int | None] = mapped_column(_FK_INT, ForeignKey("designs.id"),
                                                  nullable=True, index=True)
    design_no: Mapped[str] = mapped_column(String(50))          # snapshot
    design_name: Mapped[str] = mapped_column(String(200))       # snapshot
    category: Mapped[str] = mapped_column(String(100))          # snapshot
    hsn_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    room_label: Mapped[str | None] = mapped_column(String(60), nullable=True)
    length_in: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    breadth_in: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    quantity: Mapped[int] = mapped_column(Integer)
    raw_sqft: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    billable_sqft: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    min_rule_applied: Mapped[bool] = mapped_column(Boolean, default=False)  # BR-SQFT-07
    line_area: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    rate_paise: Mapped[int] = mapped_column(BigInteger)
    rate_source: Mapped[str] = mapped_column(String(10))        # special|base (BR-PR-02)
    making_charge_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    line_discount_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    order_discount_share_paise: Mapped[int] = mapped_column(BigInteger, default=0)  # BR-PR-09
    taxable_paise: Mapped[int] = mapped_column(BigInteger)
    gst_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    cgst_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    sgst_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    igst_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    line_total_paise: Mapped[int] = mapped_column(BigInteger)

    order: Mapped[Order] = relationship(back_populates="items")


class OrderStatusHistory(Base, PKMixin):
    """Append-only (BR-ORD-03)."""

    __tablename__ = "order_status_history"

    order_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("orders.id"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    to_status: Mapped[str] = mapped_column(String(30))
    actor_type: Mapped[str] = mapped_column(String(20))
    actor_id: Mapped[int | None] = mapped_column(_FK_INT, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    order: Mapped[Order] = relationship(back_populates="status_history")


class Quotation(Base, PKMixin, TimestampMixin, ActorMixin):
    """BR-ORD-05: no credit check until converted."""

    __tablename__ = "quotations"

    quote_no: Mapped[str] = mapped_column(String(30), unique=True)
    customer_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("customers.id"), index=True)
    status: Mapped[str] = mapped_column(String(15), default="open")  # open|converted|expired
    grand_total_paise: Mapped[int] = mapped_column(BigInteger)
    converted_order_id: Mapped[int | None] = mapped_column(_FK_INT, ForeignKey("orders.id"),
                                                           nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(_JSON)  # the cart input, re-priced on convert
