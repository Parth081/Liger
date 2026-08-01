"""Customer scoring — BR-SCR-01…07. Suggests; never changes a limit (BR-SCR-05)."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import settings_registry
from app.core.money import Money
from app.modules.credit.exposure import cash_ratio
from app.modules.credit.models import CreditEvent, CustomerScore, Invoice, LedgerEntry
from app.modules.customers.models import Customer
from app.modules.orders.models import Order

BANDS = [(85, "A+", Decimal("3.0")), (70, "A", Decimal("2.5")), (55, "B", Decimal("2.0")),
         (40, "C", Decimal("1.5")), (0, "D", Decimal("1.0"))]  # BR-SCR-03/04 multipliers


def _band(score: int) -> tuple[str, Decimal]:
    for cutoff, band, mult in BANDS:
        if score >= cutoff:
            return band, mult
    return "D", Decimal("1.0")


def compute_score(db: Session, customer: Customer, on: date) -> CustomerScore:
    """Each factor scores 0–100, then weights (Settings, BR-SCR-02) apply.
    Factors carry a plain-language reason (BR-SCR-06)."""
    factors: dict[str, dict] = {}
    year_ago = on - timedelta(days=365)

    first_order_date = (
        db.query(func.min(Order.order_date))
        .filter(Order.customer_id == customer.id)
        .scalar()
    )
    # BR-SCR-07: under 3 months of history -> NEW, no suggestion
    if first_order_date is None or (on - first_order_date).days < 90:
        score_row = CustomerScore(customer_id=customer.id, computed_on=on, score=0,
                                  band="NEW", factors={"reason": "Less than 3 months of history"},
                                  suggested_limit_paise=None)
        _store(db, score_row)
        return score_row

    # 1. Punctuality (30%): paid invoices — avg days late vs credit_days
    paid = (
        db.query(Invoice)
        .filter(Invoice.customer_id == customer.id, Invoice.status == "paid",
                Invoice.invoice_date >= year_ago)
        .all()
    )
    if paid:
        # proxy: updated_at approximates settlement date
        lates = [max(0, (inv.updated_at.date() - inv.due_date).days) for inv in paid]
        avg_late = sum(lates) / len(lates)
        punctuality = max(0, 100 - int(avg_late * 100 / max(customer.credit_days, 1)))
        reason = (f"pays {avg_late:.0f} days late on average" if avg_late >= 1
                  else "pays on time or early")
    else:
        punctuality, reason = 50, "no settled invoices in the last 12 months"
    factors["punctuality"] = {"score": punctuality, "reason": reason}

    # 2. Overdue history (20%): blocks/red events in 12 months
    bad_events = (
        db.query(func.count(CreditEvent.id))
        .filter(CreditEvent.customer_id == customer.id,
                CreditEvent.event_type == "blocked",
                CreditEvent.is_shadow.is_(False),
                CreditEvent.created_at >= year_ago)
        .scalar()
    )
    overdue_score = max(0, 100 - int(bad_events) * 40)
    factors["overdue_history"] = {
        "score": overdue_score,
        "reason": f"blocked {bad_events} time(s) in 12 months" if bad_events else "never blocked",
    }

    # 3. Volume (20%): trailing-12-month purchases vs ₹10L reference
    volume_paise = int(
        db.query(func.coalesce(func.sum(Order.grand_total_paise), 0))
        .filter(Order.customer_id == customer.id, Order.order_date >= year_ago,
                Order.status.notin_(("CANCELLED", "DRAFT")))
        .scalar()
    )
    volume_score = min(100, int(volume_paise / 1_000_000))  # 100 at ₹10L+/yr
    factors["volume"] = {"score": volume_score,
                         "reason": f"{Money(volume_paise).format_inr()} purchased in 12 months"}

    # 4. Consistency (10%): months with ≥1 order out of last 12
    months_active = (
        db.query(func.count(func.distinct(func.strftime("%Y-%m", Order.order_date)))
                 if db.get_bind().dialect.name == "sqlite"
                 else func.count(func.distinct(func.to_char(Order.order_date, "YYYY-MM"))))
        .filter(Order.customer_id == customer.id, Order.order_date >= year_ago)
        .scalar()
    )
    consistency = min(100, int(months_active) * 100 // 12)
    factors["consistency"] = {"score": consistency,
                              "reason": f"ordered in {months_active} of the last 12 months"}

    # 5. Cash ratio (10%)
    ratio = cash_ratio(db, customer.id, on)
    cash_score = min(100, int(ratio * 200))  # 50%+ cash -> full marks
    factors["cash_ratio"] = {"score": cash_score,
                             "reason": f"{ratio:.0%} of recent payments in confirmed cash"}

    # 6. Tenure (5%)
    tenure_years = (on - first_order_date).days / 365
    tenure_score = min(100, int(tenure_years * 25))
    factors["tenure"] = {"score": tenure_score,
                         "reason": f"customer for {tenure_years:.1f} years"}

    # 7. Disputes (5%): reversal entries in 12 months (bounces/chargebacks)
    reversals = (
        db.query(func.count(LedgerEntry.id))
        .filter(LedgerEntry.customer_id == customer.id,
                LedgerEntry.entry_type == "reversal",
                LedgerEntry.posted_at >= year_ago)
        .scalar()
    )
    dispute_score = max(0, 100 - int(reversals) * 50)
    factors["disputes"] = {"score": dispute_score,
                           "reason": f"{reversals} payment reversal(s) in 12 months"}

    weights = {
        "punctuality": settings_registry.get_int(db, "score_w_punctuality"),
        "overdue_history": settings_registry.get_int(db, "score_w_overdue"),
        "volume": settings_registry.get_int(db, "score_w_volume"),
        "consistency": settings_registry.get_int(db, "score_w_consistency"),
        "cash_ratio": settings_registry.get_int(db, "score_w_cash"),
        "tenure": settings_registry.get_int(db, "score_w_tenure"),
        "disputes": settings_registry.get_int(db, "score_w_disputes"),
    }
    total_weight = sum(weights.values())
    score = round(sum(factors[k]["score"] * w for k, w in weights.items()) / total_weight)

    band, multiplier = _band(score)

    # BR-SCR-04: suggested = multiplier × trailing-3-month avg monthly purchase
    three_months_ago = on - timedelta(days=91)
    recent_paise = int(
        db.query(func.coalesce(func.sum(Order.grand_total_paise), 0))
        .filter(Order.customer_id == customer.id, Order.order_date >= three_months_ago,
                Order.status.notin_(("CANCELLED", "DRAFT")))
        .scalar()
    )
    monthly_avg = Money(recent_paise // 3)
    ceiling = Money(settings_registry.get_int(db, "global_limit_ceiling_paise"))
    suggested = monthly_avg * multiplier
    if suggested > ceiling:
        suggested = ceiling

    score_row = CustomerScore(customer_id=customer.id, computed_on=on, score=score,
                              band=band, factors=factors,
                              suggested_limit_paise=suggested.paise)
    _store(db, score_row)
    return score_row


def _store(db: Session, row: CustomerScore) -> None:
    existing = (
        db.query(CustomerScore)
        .filter(CustomerScore.customer_id == row.customer_id,
                CustomerScore.computed_on == row.computed_on)
        .first()
    )
    if existing is not None:
        db.delete(existing)
        db.flush()
    db.add(row)
    db.commit()
