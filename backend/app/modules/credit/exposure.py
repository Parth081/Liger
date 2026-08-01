"""Exposure, ageing and effective-limit computation (BR-CR-01…07)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import settings_registry
from app.core.money import Money
from app.modules.credit import ledger
from app.modules.credit.models import CreditOverride, Invoice, LedgerEntry
from app.modules.customers.models import Customer
from app.modules.orders.models import Order

# Order states that consume credit before invoicing (BR-CR-02)
_EXPOSED_STATES = ("CONFIRMED", "IN_PRODUCTION", "READY", "DISPATCHED", "PARTIALLY_DELIVERED")


@dataclass
class AgeingBucket:
    label: str
    amount: Money


@dataclass
class OverdueInvoice:
    invoice_no: str
    amount_paise: int
    due_date: date
    days_overdue: int


@dataclass
class CreditPosition:
    outstanding: Money           # BR-CR-01: from the ledger
    uninvoiced_orders: Money     # BR-CR-02
    exposure: Money
    base_limit: Money
    cash_bonus: Money            # BR-CR-06
    override_extra: Money        # BR-CR-50
    effective_limit: Money       # BR-CR-05
    available: Money             # BR-CR-07 — may be negative, never clamped
    buckets: list[AgeingBucket] = field(default_factory=list)   # BR-CR-04
    overdue_invoices: list[OverdueInvoice] = field(default_factory=list)
    max_days_overdue: int = 0


def uninvoiced_order_total(db: Session, customer_id: int) -> Money:
    """BR-CR-02: confirmed-but-uninvoiced orders consume credit immediately —
    otherwise ten same-day orders beat the check."""
    invoiced_order_ids = (
        db.query(Invoice.order_id)
        .filter(Invoice.customer_id == customer_id, Invoice.order_id.isnot(None),
                Invoice.status != "cancelled")
    )
    total = (
        db.query(func.coalesce(func.sum(Order.grand_total_paise), 0))
        .filter(
            Order.customer_id == customer_id,
            Order.status.in_(_EXPOSED_STATES),
            Order.is_prepaid.is_(False),          # BR-CR-12: prepaid consumes nothing
            ~Order.id.in_(invoiced_order_ids),
        )
        .scalar()
    )
    return Money(int(total))


def cash_ratio(db: Session, customer_id: int, on: date) -> Decimal:
    """BR-CR-06: confirmed-cash share of payments, trailing 6 months.
    Reads ledger payment entries; P4 posts them with meta {"method": ...}."""
    from datetime import timedelta

    since = on - timedelta(days=183)
    rows = (
        db.query(LedgerEntry.credit_paise, LedgerEntry.meta)
        .filter(
            LedgerEntry.customer_id == customer_id,
            LedgerEntry.entry_type == "payment",
            LedgerEntry.posted_at >= since,
        )
        .all()
    )
    total = sum(r[0] for r in rows)
    if total == 0:
        return Decimal(0)
    cash = sum(r[0] for r in rows if (r[1] or {}).get("method") == "cash")
    return Decimal(cash) / Decimal(total)


def ageing(db: Session, customer_id: int, on: date) -> tuple[list[AgeingBucket], list[OverdueInvoice], int]:
    """BR-CR-04 buckets per unpaid invoice, on its own due date."""
    invoices = (
        db.query(Invoice)
        .filter(Invoice.customer_id == customer_id, Invoice.status == "open")
        .all()
    )
    buckets = {"current": 0, "1-30": 0, "31-60": 0, "61-90": 0, "90+": 0}
    overdue: list[OverdueInvoice] = []
    max_days = 0
    for inv in invoices:
        outstanding = inv.outstanding_paise
        if outstanding <= 0:
            continue
        days = (on - inv.due_date).days
        if days <= 0:
            buckets["current"] += outstanding
        else:
            max_days = max(max_days, days)
            overdue.append(OverdueInvoice(inv.invoice_no, outstanding, inv.due_date, days))
            if days <= 30:
                buckets["1-30"] += outstanding
            elif days <= 60:
                buckets["31-60"] += outstanding
            elif days <= 90:
                buckets["61-90"] += outstanding
            else:
                buckets["90+"] += outstanding
    overdue.sort(key=lambda o: o.due_date)
    return (
        [AgeingBucket(label, Money(amt)) for label, amt in buckets.items()],
        overdue,
        max_days,
    )


def compute_position(db: Session, customer: Customer, on: date) -> CreditPosition:
    outstanding = ledger.current_balance(db, customer.id)
    uninvoiced = uninvoiced_order_total(db, customer.id)
    exposure = outstanding + uninvoiced

    base = Money(customer.credit_limit_paise)

    # BR-CR-06: bonus only while the confirmed-cash ratio clears the threshold
    bonus = Money.zero()
    threshold = settings_registry.get_decimal(db, "cash_ratio_threshold")
    if customer.cash_bonus_pct and cash_ratio(db, customer.id, on) >= threshold:
        bonus = base.percent(Decimal(customer.cash_bonus_pct))

    # BR-CR-50: active overrides
    extra = (
        db.query(func.coalesce(func.sum(CreditOverride.extra_limit_paise), 0))
        .filter(
            CreditOverride.customer_id == customer.id,
            CreditOverride.valid_until >= on,
            CreditOverride.revoked_at.is_(None),
        )
        .scalar()
    )
    override_extra = Money(int(extra))

    effective = base + bonus + override_extra
    buckets, overdue, max_days = ageing(db, customer.id, on)

    return CreditPosition(
        outstanding=outstanding,
        uninvoiced_orders=uninvoiced,
        exposure=exposure,
        base_limit=base,
        cash_bonus=bonus,
        override_extra=override_extra,
        effective_limit=effective,
        available=effective - exposure,
        buckets=buckets,
        overdue_invoices=overdue,
        max_days_overdue=max_days,
    )


def colour_state(position: CreditPosition, customer_status: str) -> str:
    """BR-CR-30…33 — ONE resolver for web, admin and app."""
    if customer_status == "blocked":
        return "blocked"
    limit = position.effective_limit.paise
    utilisation = (position.exposure.paise / limit) if limit > 0 else (
        1.0 if position.exposure.paise > 0 else 0.0
    )
    days = position.max_days_overdue
    # days > 45 normally means blocked; in shadow mode the customer may still
    # be unblocked — render red, never green.
    if utilisation >= 0.9 or days >= 16:
        return "red"
    if utilisation >= 0.6 or days >= 1:
        return "amber"
    return "green"
