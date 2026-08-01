"""The credit gate — BR-CR-10…16, evaluated in exact rule order, with shadow
mode (BR-CR-40). Same signature as the P2 stub; the order service call site
did not change.

The returned decision object is stored on every order (BR-CR-21) so
"why was this allowed?" is always answerable. The decision is never computed
client-side (BR-CR-22).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.core import settings_registry
from app.core.money import Money
from app.db.base import utcnow
from app.modules.credit.exposure import compute_position
from app.modules.credit.models import CreditEvent
from app.modules.customers.models import Customer

ALLOW = "ALLOW"
WARN = "WARN"
BLOCK = "BLOCK"
NEEDS_APPROVAL = "NEEDS_APPROVAL"


@dataclass
class CreditDecision:
    decision: str
    reasons: list[str] = field(default_factory=list)
    effective_limit_paise: int = 0
    exposure_paise: int = 0
    available_paise: int = 0
    outstanding_paise: int = 0
    overdue_invoices: list[dict[str, Any]] = field(default_factory=list)
    suggested_payment_paise: int = 0
    shadow: bool = False                     # BR-CR-40: computed but not enforced

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


def lock_customer(db: Session, customer_id: int) -> Customer | None:
    """R7 / BR-CR-20: row lock — concurrent orders serialize here."""
    return (
        db.query(Customer)
        .filter(Customer.id == customer_id, Customer.deleted_at.is_(None))
        .with_for_update()
        .first()
    )


def _raw_decision(db: Session, customer: Customer, order_total: Money,
                  is_prepaid: bool, on: date) -> CreditDecision:
    """BR-CR-10…16 in exact order, first match wins."""
    position = compute_position(db, customer, on)
    base: dict[str, Any] = dict(
        effective_limit_paise=position.effective_limit.paise,
        exposure_paise=position.exposure.paise,
        available_paise=position.available.paise,
        outstanding_paise=position.outstanding.paise,
        overdue_invoices=[
            {"invoice_no": o.invoice_no, "amount_paise": o.amount_paise,
             "due_date": str(o.due_date), "days_overdue": o.days_overdue}
            for o in position.overdue_invoices
        ],
    )

    # BR-CR-10: blocked customer (manual or automatic)
    if customer.status == "blocked":
        return CreditDecision(
            decision=BLOCK,
            reasons=["MANUAL_BLOCK" if customer.is_manual_block else "BLOCKED"],
            suggested_payment_paise=sum(o.amount_paise for o in position.overdue_invoices)
            or position.outstanding.paise,
            **base,
        )

    # BR-CR-11: any invoice overdue beyond hard_block_days — age, not amount
    hard_days = settings_registry.get_int(db, "hard_block_days")
    hard_overdue = [o for o in position.overdue_invoices if o.days_overdue > hard_days]
    if hard_overdue:
        return CreditDecision(
            decision=BLOCK,
            reasons=[f"OVERDUE_BEYOND_{hard_days}_DAYS"],
            suggested_payment_paise=sum(o.amount_paise for o in hard_overdue),
            **base,
        )

    # BR-CR-12: fully prepaid — consumes no credit
    if is_prepaid:
        return CreditDecision(decision=ALLOW, reasons=["PREPAID"], **base)

    # BR-CR-13: order exceeds available credit — approval path, not dead end
    if order_total > position.available:
        shortfall = order_total - position.available
        return CreditDecision(
            decision=NEEDS_APPROVAL,
            reasons=["LIMIT_EXCEEDED"],
            suggested_payment_paise=shortfall.paise,
            **base,
        )

    reasons: list[str] = []
    # BR-CR-14: pushes exposure past the warn threshold
    warn_pct = settings_registry.get_decimal(db, "warn_utilisation_pct")
    limit = position.effective_limit.paise
    if limit > 0:
        after = position.exposure.paise + order_total.paise
        if after * 100 >= limit * int(warn_pct):
            reasons.append("UTILISATION_ABOVE_80")

    # BR-CR-15: overdue within the hard window — warn (or block by setting)
    if position.overdue_invoices:
        if settings_registry.get_bool(db, "overdue_soft_block"):
            return CreditDecision(
                decision=BLOCK, reasons=["OVERDUE_SOFT_BLOCK"],
                suggested_payment_paise=sum(o.amount_paise for o in position.overdue_invoices),
                **base,
            )
        reasons.append("HAS_OVERDUE")

    # BR-CR-16
    return CreditDecision(decision=WARN if reasons else ALLOW, reasons=reasons, **base)


def evaluate(
    db: Session,
    customer: Customer,
    order_total: Money,
    *,
    is_prepaid: bool = False,
    on: date | None = None,
) -> CreditDecision:
    on = on or date.today()
    decision = _raw_decision(db, customer, order_total, is_prepaid, on)

    mode = settings_registry.get_str(db, "credit_enforcement_mode")
    if mode == "shadow" and decision.decision in (BLOCK, NEEDS_APPROVAL):
        # BR-CR-52: a manual admin block is a human decision — enforced even in shadow
        if not (customer.status == "blocked" and customer.is_manual_block):
            db.add(CreditEvent(
                customer_id=customer.id,
                event_type="gate_decision",
                detail=decision.as_json(),
                reason="shadow-mode: would have " + decision.decision,
                is_shadow=True,
                created_at=utcnow(),
            ))  # BR-CR-40: log it, allow it — the owner reviews before enforcement
            db.flush()
            allowed = CreditDecision(
                decision=WARN,
                reasons=[f"SHADOW_{decision.decision}", *decision.reasons],
                effective_limit_paise=decision.effective_limit_paise,
                exposure_paise=decision.exposure_paise,
                available_paise=decision.available_paise,
                outstanding_paise=decision.outstanding_paise,
                overdue_invoices=decision.overdue_invoices,
                suggested_payment_paise=decision.suggested_payment_paise,
                shadow=True,
            )
            return allowed
    return decision
