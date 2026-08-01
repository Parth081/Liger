"""Customer master & regions (DATA_MODEL §2). No price tier — DEC-03."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import (
    ActorMixin,
    Base,
    PKMixin,
    SoftDeleteMixin,
    TimestampMixin,
    VersionMixin,
)

_FK_INT = BigInteger().with_variant(Integer, "sqlite")

# BR-CR-30…33 colour states
CUSTOMER_STATUS = sa.Enum("active", "warned", "red", "blocked", name="customer_status")


class Region(Base, PKMixin, TimestampMixin):
    __tablename__ = "regions"

    parent_id: Mapped[int | None] = mapped_column(_FK_INT, ForeignKey("regions.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(20))  # zone|state|city|territory
    code: Mapped[str | None] = mapped_column(String(20), nullable=True)


class Customer(Base, PKMixin, TimestampMixin, ActorMixin, VersionMixin, SoftDeleteMixin):
    __tablename__ = "customers"

    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    business_name: Mapped[str] = mapped_column(String(200), index=True)
    legal_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)  # BR-TAX-04
    pan: Mapped[str | None] = mapped_column(String(10), nullable=True)
    primary_phone: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Geography / distribution (BR-AN-03)
    region_id: Mapped[int | None] = mapped_column(_FK_INT, ForeignKey("regions.id"), nullable=True, index=True)
    state: Mapped[str | None] = mapped_column(String(40), nullable=True)  # BR-TAX-02 place of supply
    city: Mapped[str | None] = mapped_column(String(80), nullable=True)
    pincode: Mapped[str | None] = mapped_column(String(10), nullable=True)
    distributor_id: Mapped[int | None] = mapped_column(_FK_INT, ForeignKey("customers.id"),
                                                       nullable=True, index=True)
    sales_rep_id: Mapped[int | None] = mapped_column(_FK_INT, ForeignKey("users.id"),
                                                     nullable=True, index=True)  # BR-AC-04 scoping

    # Credit engine fields (BR-CR-*) — money in paise (R1)
    credit_limit_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    credit_days: Mapped[int] = mapped_column(Integer, default=30)  # DEC-06
    cash_bonus_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("10"))  # DEC-08
    opening_balance_paise: Mapped[int] = mapped_column(BigInteger, default=0)  # BR-LED-05
    status: Mapped[str] = mapped_column(CUSTOMER_STATUS, default="active", index=True)
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    block_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    unblocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_manual_block: Mapped[bool] = mapped_column(Boolean, default=False)  # BR-CR-52

    language: Mapped[str] = mapped_column(String(5), default="en")  # DEC-10
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    region: Mapped[Region | None] = relationship(lazy="joined")


class CustomerAddress(Base, PKMixin, TimestampMixin):
    __tablename__ = "customer_addresses"

    customer_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("customers.id"), index=True)
    type: Mapped[str] = mapped_column(String(10))  # billing|shipping
    line1: Mapped[str] = mapped_column(String(200))
    line2: Mapped[str | None] = mapped_column(String(200), nullable=True)
    state: Mapped[str | None] = mapped_column(String(40), nullable=True)
    city: Mapped[str | None] = mapped_column(String(80), nullable=True)
    pincode: Mapped[str | None] = mapped_column(String(10), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class CustomerContact(Base, PKMixin, TimestampMixin):
    __tablename__ = "customer_contacts"

    customer_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("customers.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20), default="owner")  # owner|accounts|site
    phone: Mapped[str] = mapped_column(String(20))
    whatsapp_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)  # BR-NOT-09
    opt_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
