"""Payment models (DATA_MODEL §6): payments, allocations, gateway events,
payment links, credit notes."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PKMixin, TimestampMixin

_FK_INT = BigInteger().with_variant(Integer, "sqlite")
_JSON = JSON().with_variant(JSONB, "postgresql")

PAYMENT_STATUS = sa.Enum(
    "initiated", "pending_confirmation", "confirmed", "failed", "reversed",
    name="payment_status",
)
PAYMENT_METHOD = sa.Enum(
    "upi", "card", "netbanking", "wallet", "cash", "cheque", "bank_transfer",
    name="payment_method",
)


class Payment(Base, PKMixin, TimestampMixin):
    __tablename__ = "payments"
    __table_args__ = (Index("ix_payments_customer_status", "customer_id", "status"),)

    customer_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("customers.id"), index=True)
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    method: Mapped[str] = mapped_column(PAYMENT_METHOD)
    status: Mapped[str] = mapped_column(PAYMENT_STATUS, default="initiated")
    # online
    gateway: Mapped[str | None] = mapped_column(String(20), nullable=True)
    gateway_order_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    gateway_payment_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # offline
    reference_no: Mapped[str | None] = mapped_column(String(80), nullable=True)
    slip_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # cash gate (BR-PAY-05)
    recorded_by: Mapped[int | None] = mapped_column(_FK_INT, ForeignKey("users.id"), nullable=True)
    confirmed_by: Mapped[int | None] = mapped_column(_FK_INT, ForeignKey("users.id"), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reversal_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    notes: Mapped[str | None] = mapped_column(String(255), nullable=True)

    allocations: Mapped[list[PaymentAllocation]] = relationship(back_populates="payment",
                                                                lazy="selectin")


class PaymentAllocation(Base, PKMixin):
    """BR-PAY-06: how a payment maps onto invoices (FIFO by default)."""

    __tablename__ = "payment_allocations"

    payment_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("payments.id"), index=True)
    invoice_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("invoices.id"), index=True)
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    payment: Mapped[Payment] = relationship(back_populates="allocations")


class GatewayEvent(Base, PKMixin):
    """BR-PAY-04: raw webhook store — idempotency + audit. Append-only by use."""

    __tablename__ = "gateway_events"

    gateway: Mapped[str] = mapped_column(String(20))
    event_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict[str, Any]] = mapped_column(_JSON)
    signature_valid: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PaymentLink(Base, PKMixin, TimestampMixin):
    """BR-PAY-10: expiring, invoice-referenced so allocation is automatic."""

    __tablename__ = "payment_links"

    customer_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("customers.id"), index=True)
    invoice_id: Mapped[int | None] = mapped_column(_FK_INT, ForeignKey("invoices.id"), nullable=True)
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    url: Mapped[str] = mapped_column(String(500))
    gateway_link_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    paid_payment_id: Mapped[int | None] = mapped_column(_FK_INT, ForeignKey("payments.id"),
                                                        nullable=True)


class CreditNote(Base, PKMixin, TimestampMixin):
    """BR-TAX-06: the ONLY way an issued invoice is reduced."""

    __tablename__ = "credit_notes"

    credit_note_no: Mapped[str] = mapped_column(String(30), unique=True)
    customer_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("customers.id"), index=True)
    invoice_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("invoices.id"), index=True)
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(String(255))
    created_by: Mapped[int | None] = mapped_column(_FK_INT, ForeignKey("users.id"), nullable=True)
