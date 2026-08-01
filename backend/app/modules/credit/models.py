"""Credit engine models (DATA_MODEL §7): append-only ledger, snapshots, events,
overrides, scores, escalation state. Plus the minimal invoice record the
ladder ages — P4 extends it with GST/items."""
from __future__ import annotations

from datetime import date, datetime
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
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin

_FK_INT = BigInteger().with_variant(Integer, "sqlite")
_JSON = JSON().with_variant(JSONB, "postgresql")

LEDGER_ENTRY_TYPE = sa.Enum(
    "opening", "invoice", "payment", "credit_note", "adjustment", "reversal",
    name="ledger_entry_type",
)  # BR-LED-02

INVOICE_STATUS = sa.Enum("open", "paid", "cancelled", name="invoice_status")


class Invoice(Base, PKMixin, TimestampMixin):
    """Minimal in P3 — enough for ageing (BR-CR-03/04). P4 adds GST columns."""

    __tablename__ = "invoices"
    __table_args__ = (
        Index("ix_invoices_customer_due", "customer_id", "due_date"),
        Index("ix_invoices_status_due", "status", "due_date"),  # the ladder's index
    )

    invoice_no: Mapped[str] = mapped_column(String(30), unique=True)
    customer_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("customers.id"), index=True)
    order_id: Mapped[int | None] = mapped_column(_FK_INT, ForeignKey("orders.id"), nullable=True)
    invoice_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)                # BR-CR-03
    total_paise: Mapped[int] = mapped_column(BigInteger)
    amount_paid_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(INVOICE_STATUS, default="open")

    @property
    def outstanding_paise(self) -> int:
        return self.total_paise - self.amount_paid_paise


class LedgerEntry(Base, PKMixin):
    """APPEND-ONLY (R2, BR-LED-01) — enforced by DB triggers in migration 0004.
    Corrections are reversing entries, never edits."""

    __tablename__ = "ledger_entries"
    __table_args__ = (Index("ix_ledger_customer_posted", "customer_id", "posted_at"),)

    customer_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("customers.id"), index=True)
    entry_type: Mapped[str] = mapped_column(LEDGER_ENTRY_TYPE)
    debit_paise: Mapped[int] = mapped_column(BigInteger, default=0)    # increases outstanding
    credit_paise: Mapped[int] = mapped_column(BigInteger, default=0)   # decreases outstanding
    balance_after_paise: Mapped[int] = mapped_column(BigInteger)       # BR-LED-02
    ref_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    ref_id: Mapped[int | None] = mapped_column(_FK_INT, nullable=True)
    meta: Mapped[dict[str, Any] | None] = mapped_column(_JSON, nullable=True)  # e.g. {"method": "cash"}
    narration: Mapped[str | None] = mapped_column(String(255), nullable=True)
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(_FK_INT, nullable=True)


class CreditSnapshot(Base, PKMixin):
    """Daily per-customer state (BR-CR-54)."""

    __tablename__ = "credit_snapshots"
    __table_args__ = (UniqueConstraint("customer_id", "as_of"),)

    customer_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("customers.id"), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    outstanding_paise: Mapped[int] = mapped_column(BigInteger)
    exposure_paise: Mapped[int] = mapped_column(BigInteger)
    effective_limit_paise: Mapped[int] = mapped_column(BigInteger)
    available_paise: Mapped[int] = mapped_column(BigInteger)
    overdue_current_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    overdue_1_30_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    overdue_31_60_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    overdue_61_90_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    overdue_90_plus_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    colour: Mapped[str] = mapped_column(String(10))            # BR-CR-30…33
    status: Mapped[str] = mapped_column(String(10))
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)


class CreditEvent(Base, PKMixin):
    """Append-only audit of every credit action (BR-CR-51). is_shadow marks
    decisions recorded but not enforced (BR-CR-40)."""

    __tablename__ = "credit_events"
    __table_args__ = (Index("ix_credit_events_customer", "customer_id", "created_at"),)

    customer_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("customers.id"))
    event_type: Mapped[str] = mapped_column(String(30))
    # warned | blocked | unblocked | limit_changed | override_granted |
    # override_expired | gate_decision | ladder_step
    detail: Mapped[dict[str, Any] | None] = mapped_column(_JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(20), default="system")
    actor_id: Mapped[int | None] = mapped_column(_FK_INT, nullable=True)
    is_shadow: Mapped[bool] = mapped_column(Boolean, default=False)     # BR-CR-40
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class CreditOverride(Base, PKMixin, TimestampMixin):
    """BR-CR-50: temporary extra limit, mandatory reason, auto-expiring."""

    __tablename__ = "credit_overrides"

    customer_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("customers.id"), index=True)
    extra_limit_paise: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(String(255))
    valid_until: Mapped[date] = mapped_column(Date)
    granted_by: Mapped[int] = mapped_column(_FK_INT, ForeignKey("users.id"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CustomerScore(Base, PKMixin):
    """BR-SCR-01: nightly time series."""

    __tablename__ = "customer_scores"
    __table_args__ = (UniqueConstraint("customer_id", "computed_on"),)

    customer_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("customers.id"), index=True)
    computed_on: Mapped[date] = mapped_column(Date)
    score: Mapped[int] = mapped_column(Integer)                 # 0–100
    band: Mapped[str] = mapped_column(String(4))                # A+|A|B|C|D|NEW
    factors: Mapped[dict[str, Any]] = mapped_column(_JSON)      # BR-SCR-06 breakdown
    suggested_limit_paise: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class EscalationState(Base, PKMixin):
    """One row per (invoice, step) — makes the ladder idempotent (BR-CR-49)."""

    __tablename__ = "escalation_state"
    __table_args__ = (UniqueConstraint("invoice_id", "step"),)

    invoice_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("invoices.id"), index=True)
    customer_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("customers.id"), index=True)
    step: Mapped[str] = mapped_column(String(15))  # pre_due|due_today|warn1|warn2|block
    fired_on: Mapped[date] = mapped_column(Date)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)  # P5 flips on real send
