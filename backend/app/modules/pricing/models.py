"""Pricing models (DATA_MODEL §4): versioned rate cards (no tiers — DEC-03),
customer special rates, making charges."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import ActorMixin, Base, PKMixin, TimestampMixin

_FK_INT = BigInteger().with_variant(Integer, "sqlite")

RATE_CARD_STATUS = sa.Enum("draft", "published", "archived", name="rate_card_status")
MAKING_MODE = sa.Enum("per_sqft", "per_piece", name="making_mode")


class RateCard(Base, PKMixin, TimestampMixin, ActorMixin):
    """BR-PR-03: versioned; publishing never alters existing orders (R9)."""

    __tablename__ = "rate_cards"

    version: Mapped[int] = mapped_column(Integer, unique=True)
    name: Mapped[str] = mapped_column(String(100))
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(RATE_CARD_STATUS, default="draft", index=True)
    published_by: Mapped[int | None] = mapped_column(_FK_INT, ForeignKey("users.id"), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    items: Mapped[list[RateCardItem]] = relationship(back_populates="rate_card", lazy="selectin")


class RateCardItem(Base, PKMixin):
    """DEC-03: one rate per design, identical for every customer."""

    __tablename__ = "rate_card_items"
    __table_args__ = (UniqueConstraint("rate_card_id", "design_id"),)

    rate_card_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("rate_cards.id"), index=True)
    design_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("designs.id"), index=True)
    rate_paise: Mapped[int] = mapped_column(BigInteger)

    rate_card: Mapped[RateCard] = relationship(back_populates="items")


class CustomerSpecialRate(Base, PKMixin, TimestampMixin, ActorMixin):
    """BR-PR-04: admin exception tool, auto-expiring."""

    __tablename__ = "customer_special_rates"

    customer_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("customers.id"), index=True)
    design_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("designs.id"), index=True)
    rate_paise: Mapped[int] = mapped_column(BigInteger)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date] = mapped_column(Date)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_by: Mapped[int | None] = mapped_column(_FK_INT, ForeignKey("users.id"), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class MakingCharge(Base, PKMixin, TimestampMixin):
    """BR-PR-06 / DEC-05: making/stitching per product type."""

    __tablename__ = "making_charges"

    product_type: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    mode: Mapped[str] = mapped_column(MAKING_MODE, default="per_sqft")
    amount_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# Convenience: Decimal alias used across pricing schemas
__all__ = ["RateCard", "RateCardItem", "CustomerSpecialRate", "MakingCharge", "Decimal"]
