"""Analytics — BR-AN-01…09.

Two rules shape this module:
  * BR-AN-04: every figure drills down to the orders behind it. A number you
    cannot open is a number nobody trusts.
  * BR-AN-08: aggregates read from reporting tables, never live scans under load.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from app.core.money import Money
from app.modules.credit.exposure import colour_state, compute_position
from app.modules.credit.ledger import derived_balance
from app.modules.credit.models import CustomerScore, Invoice
from app.modules.customers.models import Customer
from app.modules.orders.models import Order, OrderItem
from app.modules.payments.models import Payment

# Orders that count as business done (BR-AN-05 note: cancelled/draft excluded)
LIVE_STATES = ("CONFIRMED", "IN_PRODUCTION", "READY", "DISPATCHED",
               "PARTIALLY_DELIVERED", "DELIVERED", "CLOSED")

GROUPABLE = {
    "month": "month", "customer": "customer", "region": "region", "state": "state",
    "distributor": "distributor", "rep": "rep", "design": "design",
    "category": "category",
}


@dataclass
class Period:
    start: date
    end: date


def _month_expr(db: Session):
    """Portable YYYY-MM bucket."""
    if db.get_bind().dialect.name == "sqlite":
        return func.strftime("%Y-%m", Order.order_date)
    return func.to_char(Order.order_date, "YYYY-MM")


def _orders_base(db: Session, period: Period, *, rep_id: int | None = None):
    q = (
        db.query(Order)
        .filter(Order.status.in_(LIVE_STATES),
                Order.order_date >= period.start, Order.order_date <= period.end)
    )
    if rep_id is not None:                      # BR-AC-04 scoping
        q = q.join(Customer, Customer.id == Order.customer_id).filter(
            Customer.sales_rep_id == rep_id)
    return q


def _sum_orders(db: Session, period: Period, *, rep_id: int | None = None) -> tuple[int, int]:
    row = (
        _orders_base(db, period, rep_id=rep_id)
        .with_entities(func.count(Order.id),
                       func.coalesce(func.sum(Order.grand_total_paise), 0))
        .one()
    )
    return int(row[0]), int(row[1])


# ---------------- dashboard (BR-AN-01) ----------------
def dashboard(db: Session, on: date, *, rep_id: int | None = None) -> dict[str, Any]:
    month_start = on.replace(day=1)
    last_month_end = month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)
    ly_start = month_start.replace(year=month_start.year - 1)
    ly_end = date(ly_start.year, ly_start.month, last_month_end.day) \
        if ly_start.month == last_month_start.month else \
        (month_start.replace(year=month_start.year - 1, day=28) + timedelta(days=4))
    ly_end = (ly_start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

    fy_start = date(on.year if on.month >= 4 else on.year - 1, 4, 1)
    quarter_start = date(on.year, 3 * ((on.month - 1) // 3) + 1, 1)

    mtd_count, mtd_value = _sum_orders(db, Period(month_start, on), rep_id=rep_id)
    lm_count, lm_value = _sum_orders(db, Period(last_month_start, last_month_end),
                                     rep_id=rep_id)
    ly_count, ly_value = _sum_orders(db, Period(ly_start, ly_end), rep_id=rep_id)
    _, qtd_value = _sum_orders(db, Period(quarter_start, on), rep_id=rep_id)
    _, ytd_value = _sum_orders(db, Period(fy_start, on), rep_id=rep_id)

    customers = db.query(Customer).filter(Customer.deleted_at.is_(None))
    if rep_id is not None:
        customers = customers.filter(Customer.sales_rep_id == rep_id)
    customer_rows = customers.all()

    outstanding = 0
    blocked_revenue = 0
    ageing = {"current": 0, "1-30": 0, "31-60": 0, "61-90": 0, "90+": 0}
    colours = {"green": 0, "amber": 0, "red": 0, "blocked": 0}
    for customer in customer_rows:
        position = compute_position(db, customer, on)
        outstanding += position.outstanding.paise
        for bucket in position.buckets:
            ageing[bucket.label] += bucket.amount.paise
        colour = colour_state(position, customer.status)
        colours[colour] = colours.get(colour, 0) + 1
        if customer.status == "blocked":
            blocked_revenue += position.outstanding.paise

    overdue = sum(v for k, v in ageing.items() if k != "current")

    # Collection efficiency: collected this month vs what fell due this month
    collected = int(
        db.query(func.coalesce(func.sum(Payment.amount_paise), 0))
        .filter(Payment.status == "confirmed", Payment.confirmed_at.isnot(None),
                func.date(Payment.confirmed_at) >= month_start,
                func.date(Payment.confirmed_at) <= on)
        .scalar()
    )
    due_this_month = int(
        db.query(func.coalesce(func.sum(Invoice.total_paise), 0))
        .filter(Invoice.due_date >= month_start, Invoice.due_date <= on)
        .scalar()
    )
    efficiency = round(collected * 100 / due_this_month, 1) if due_this_month else None

    return {
        "as_of": str(on),
        "sales": {
            "mtd_paise": mtd_value, "mtd_orders": mtd_count,
            "last_month_paise": lm_value, "last_month_orders": lm_count,
            "same_month_last_year_paise": ly_value, "same_month_last_year_orders": ly_count,
            "mom_change_pct": _pct_change(lm_value, mtd_value),
            "yoy_change_pct": _pct_change(ly_value, mtd_value),
            "qtd_paise": qtd_value, "ytd_paise": ytd_value,
        },
        "money": {
            "outstanding_paise": outstanding,
            "overdue_paise": overdue,
            "blocked_revenue_paise": blocked_revenue,
            "collected_mtd_paise": collected,
            "collection_efficiency_pct": efficiency,
            "dso_days": days_sales_outstanding(db, on, rep_id=rep_id),
        },
        "ageing": ageing,
        "customers": {"total": len(customer_rows), **colours},
    }


def _pct_change(base: int, current: int) -> float | None:
    if base == 0:
        return None
    return round((current - base) * 100 / base, 1)


def days_sales_outstanding(db: Session, on: date, *, rep_id: int | None = None) -> float | None:
    """DSO = outstanding / avg daily sales over the trailing 90 days."""
    window = Period(on - timedelta(days=90), on)
    _, sales = _sum_orders(db, window, rep_id=rep_id)
    if sales == 0:
        return None
    customers = db.query(Customer).filter(Customer.deleted_at.is_(None))
    if rep_id is not None:
        customers = customers.filter(Customer.sales_rep_id == rep_id)
    outstanding = sum(derived_balance(db, c.id).paise for c in customers.all())
    return round(outstanding / (sales / 90), 1)


# ---------------- slice & dice (BR-AN-02) ----------------
def sales_by(db: Session, period: Period, group_by: list[str], *,
             rep_id: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Any combination of month/customer/region/state/distributor/rep/design/category."""
    unknown = [g for g in group_by if g not in GROUPABLE]
    if unknown:
        from app.core.exceptions import ValidationFailed

        raise ValidationFailed(f"Cannot group by: {', '.join(unknown)}",
                               {"allowed": sorted(GROUPABLE)})

    needs_items = any(g in ("design", "category") for g in group_by)
    distributor = None

    q = db.query()
    columns: list = []
    labels: list[str] = []

    for g in group_by:
        if g == "month":
            columns.append(_month_expr(db).label("month"))
        elif g == "customer":
            columns.append(Customer.business_name.label("customer"))
        elif g == "region":
            columns.append(func.coalesce(Customer.city, Customer.state, "Unknown").label("region"))
        elif g == "state":
            columns.append(func.coalesce(Customer.state, "Unknown").label("state"))
        elif g == "distributor":
            from sqlalchemy.orm import aliased

            distributor = aliased(Customer)
            columns.append(func.coalesce(distributor.business_name, "Direct").label("distributor"))
        elif g == "rep":
            columns.append(func.coalesce(Customer.sales_rep_id, 0).label("rep"))
        elif g == "design":
            columns.append(OrderItem.design_no.label("design"))
        elif g == "category":
            columns.append(OrderItem.category.label("category"))
        labels.append(g)

    value_col = (func.sum(OrderItem.line_total_paise) if needs_items
                 else func.sum(Order.grand_total_paise))
    q = db.query(*columns,
                 func.count(func.distinct(Order.id)).label("orders"),
                 func.coalesce(value_col, 0).label("value_paise"))
    q = q.select_from(Order).join(Customer, Customer.id == Order.customer_id)
    if needs_items:
        q = q.join(OrderItem, OrderItem.order_id == Order.id)
    if distributor is not None:
        q = q.outerjoin(distributor, distributor.id == Customer.distributor_id)
    q = q.filter(Order.status.in_(LIVE_STATES),
                 Order.order_date >= period.start, Order.order_date <= period.end)
    if rep_id is not None:
        q = q.filter(Customer.sales_rep_id == rep_id)
    q = q.group_by(*columns).order_by(func.coalesce(value_col, 0).desc()).limit(limit)

    results = []
    for row in q.all():
        entry = {labels[i]: row[i] for i in range(len(labels))}
        entry["orders"] = int(row[len(labels)])
        entry["value_paise"] = int(row[len(labels) + 1])
        results.append(entry)
    return results


def drilldown(db: Session, period: Period, filters: dict[str, str], *,
              rep_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """BR-AN-04 — the orders behind any aggregate cell."""
    q = (
        db.query(Order, Customer)
        .join(Customer, Customer.id == Order.customer_id)
        .filter(Order.status.in_(LIVE_STATES),
                Order.order_date >= period.start, Order.order_date <= period.end)
    )
    if rep_id is not None:
        q = q.filter(Customer.sales_rep_id == rep_id)
    if "customer" in filters:
        q = q.filter(Customer.business_name == filters["customer"])
    if "state" in filters:
        q = q.filter(Customer.state == filters["state"])
    if "month" in filters:
        q = q.filter(_month_expr(db) == filters["month"])
    if "design" in filters or "category" in filters:
        q = q.join(OrderItem, OrderItem.order_id == Order.id)
        if "design" in filters:
            q = q.filter(OrderItem.design_no == filters["design"])
        if "category" in filters:
            q = q.filter(OrderItem.category == filters["category"])
    rows = q.order_by(Order.order_date.desc(), Order.id.desc()).limit(limit).all()
    return [
        {"order_uid": str(o.uid), "order_no": o.order_no, "order_date": str(o.order_date),
         "customer": c.business_name, "status": o.status,
         "grand_total_paise": o.grand_total_paise}
        for o, c in rows
    ]


# ---------------- distributor roll-up (BR-AN-03) ----------------
def distributor_rollup(db: Session, period: Period) -> list[dict[str, Any]]:
    """'Distributor X: ₹Y this month across 12 dealers in Gujarat.'"""
    from sqlalchemy.orm import aliased

    dealer = aliased(Customer)
    rows = (
        db.query(
            Customer.uid, Customer.business_name, Customer.state,
            func.count(func.distinct(dealer.id)).label("dealers"),
            func.count(func.distinct(Order.id)).label("orders"),
            func.coalesce(func.sum(Order.grand_total_paise), 0).label("value"),
        )
        .join(dealer, or_(dealer.distributor_id == Customer.id, dealer.id == Customer.id))
        .outerjoin(Order, (Order.customer_id == dealer.id)
                   & Order.status.in_(LIVE_STATES)
                   & (Order.order_date >= period.start)
                   & (Order.order_date <= period.end))
        .filter(Customer.deleted_at.is_(None))
        .group_by(Customer.id, Customer.uid, Customer.business_name, Customer.state)
        .having(func.count(func.distinct(dealer.id)) > 1)      # actual distributors only
        .order_by(func.coalesce(func.sum(Order.grand_total_paise), 0).desc())
        .all()
    )
    return [
        {"customer_uid": str(r[0]), "distributor": r[1], "state": r[2],
         "dealers": int(r[3]), "orders": int(r[4]), "value_paise": int(r[5])}
        for r in rows
    ]


# ---------------- customer 360 + nudges (BR-AN-05/06) ----------------
def customer_360(db: Session, customer: Customer, on: date) -> dict[str, Any]:
    position = compute_position(db, customer, on)
    year_ago = on - timedelta(days=365)

    monthly = (
        db.query(_month_expr(db).label("m"),
                 func.coalesce(func.sum(Order.grand_total_paise), 0))
        .filter(Order.customer_id == customer.id, Order.status.in_(LIVE_STATES),
                Order.order_date >= year_ago)
        .group_by(_month_expr(db))
        .order_by(_month_expr(db))
        .all()
    )
    stats = (
        db.query(func.count(Order.id),
                 func.coalesce(func.avg(Order.grand_total_paise), 0),
                 func.max(Order.order_date))
        .filter(Order.customer_id == customer.id, Order.status.in_(LIVE_STATES),
                Order.order_date >= year_ago)
        .one()
    )
    favourites = (
        db.query(OrderItem.design_no, OrderItem.design_name,
                 func.count(OrderItem.id).label("times"))
        .join(Order, Order.id == OrderItem.order_id)
        .filter(Order.customer_id == customer.id, Order.status.in_(LIVE_STATES),
                Order.order_date >= year_ago)
        .group_by(OrderItem.design_no, OrderItem.design_name)
        .order_by(func.count(OrderItem.id).desc())
        .limit(5)
        .all()
    )
    last_payment = (
        db.query(Payment)
        .filter(Payment.customer_id == customer.id, Payment.status == "confirmed")
        .order_by(Payment.confirmed_at.desc())
        .first()
    )
    score = (
        db.query(CustomerScore)
        .filter(CustomerScore.customer_id == customer.id)
        .order_by(CustomerScore.computed_on.desc())
        .first()
    )

    return {
        "customer": {"uid": str(customer.uid), "name": customer.business_name,
                     "phone": customer.primary_phone, "state": customer.state,
                     "city": customer.city, "status": customer.status},
        "credit": {
            "colour": colour_state(position, customer.status),
            "outstanding_paise": position.outstanding.paise,
            "exposure_paise": position.exposure.paise,
            "effective_limit_paise": position.effective_limit.paise,
            "available_paise": position.available.paise,
            "max_days_overdue": position.max_days_overdue,
            "ageing": {b.label: b.amount.paise for b in position.buckets},
        },
        "trend_12m": [{"month": m, "value_paise": int(v)} for m, v in monthly],
        "orders_12m": int(stats[0]),
        "avg_order_value_paise": int(stats[1] or 0),
        "last_order_date": str(stats[2]) if stats[2] else None,
        "last_payment": {
            "amount_paise": last_payment.amount_paise,
            "method": last_payment.method,
            "at": last_payment.confirmed_at.isoformat() if last_payment.confirmed_at else None,
        } if last_payment else None,
        "favourite_designs": [
            {"design_no": d, "design_name": n, "times": int(t)} for d, n, t in favourites
        ],
        "score": {"score": score.score, "band": score.band, "factors": score.factors,
                  "suggested_limit_paise": score.suggested_limit_paise} if score else None,
        "nudges": nudges(db, customer, on, position, stats),
    }


def nudges(db: Session, customer: Customer, on: date, position, stats) -> list[dict[str, str]]:
    """BR-AN-06 — plain-language, actionable. Each one implies a phone call."""
    out: list[dict[str, str]] = []
    order_count, last_order = int(stats[0]), stats[2]

    if last_order is not None and order_count >= 4:
        first = (
            db.query(func.min(Order.order_date))
            .filter(Order.customer_id == customer.id, Order.status.in_(LIVE_STATES))
            .scalar()
        )
        span = max((last_order - first).days, 1) if first else 1
        avg_gap = span / max(order_count - 1, 1)
        silence = (on - last_order).days
        if silence >= max(avg_gap * 2, 30):
            out.append({
                "type": "dormant",
                "message": (f"Ordered {order_count} times in the last year "
                            f"(about every {avg_gap:.0f} days) — nothing in {silence} days. "
                            "Worth a call."),
            })

    limit = position.effective_limit.paise
    if limit and position.exposure.paise * 100 >= limit * 90:
        out.append({
            "type": "limit_pressure",
            "message": (f"Using {position.exposure.paise * 100 // limit}% of their limit. "
                        "Review the limit or ask for a payment before the next order."),
        })

    score = (
        db.query(CustomerScore)
        .filter(CustomerScore.customer_id == customer.id)
        .order_by(CustomerScore.computed_on.desc())
        .first()
    )
    if score and score.band in ("A+", "A") and score.suggested_limit_paise and \
            score.suggested_limit_paise > customer.credit_limit_paise * 1.2:
        out.append({
            "type": "raise_limit",
            "message": (f"Band {score.band} and paying well — "
                        f"score suggests a limit of "
                        f"{Money(score.suggested_limit_paise).format_inr()} "
                        f"vs the current {Money(customer.credit_limit_paise).format_inr()}."),
        })

    if position.max_days_overdue >= 16:
        out.append({
            "type": "overdue",
            "message": (f"Oldest unpaid invoice is {position.max_days_overdue} days past due "
                        f"({Money(position.outstanding.paise).format_inr()} outstanding)."),
        })
    return out


# ---------------- leaderboards ----------------
def top_customers(db: Session, period: Period, limit: int = 10,
                  rep_id: int | None = None) -> list[dict[str, Any]]:
    return sales_by(db, period, ["customer"], rep_id=rep_id, limit=limit)


def outstanding_report(db: Session, on: date, *, rep_id: int | None = None) -> list[dict[str, Any]]:
    q = db.query(Customer).filter(Customer.deleted_at.is_(None))
    if rep_id is not None:
        q = q.filter(Customer.sales_rep_id == rep_id)
    rows: list[dict[str, Any]] = []
    for customer in q.all():
        position = compute_position(db, customer, on)
        if position.outstanding.paise == 0 and position.exposure.paise == 0:
            continue
        rows.append({
            "customer_uid": str(customer.uid), "customer": customer.business_name,
            "state": customer.state, "status": customer.status,
            "colour": colour_state(position, customer.status),
            "outstanding_paise": position.outstanding.paise,
            "available_paise": position.available.paise,
            "max_days_overdue": position.max_days_overdue,
            "ageing": {b.label: b.amount.paise for b in position.buckets},
        })
    rows.sort(key=lambda r: int(r["outstanding_paise"] or 0), reverse=True)
    return rows


def design_performance(db: Session, period: Period, limit: int = 20) -> list[dict[str, Any]]:
    rows = (
        db.query(OrderItem.design_no, OrderItem.design_name, OrderItem.category,
                 func.count(OrderItem.id), func.sum(OrderItem.line_area),
                 func.sum(OrderItem.line_total_paise))
        .join(Order, Order.id == OrderItem.order_id)
        .filter(Order.status.in_(LIVE_STATES),
                Order.order_date >= period.start, Order.order_date <= period.end)
        .group_by(OrderItem.design_no, OrderItem.design_name, OrderItem.category)
        .order_by(func.sum(OrderItem.line_total_paise).desc())
        .limit(limit)
        .all()
    )
    return [
        {"design_no": r[0], "design_name": r[1], "category": r[2],
         "lines": int(r[3]), "sqft": float(r[4] or 0), "value_paise": int(r[5] or 0)}
        for r in rows
    ]


def collections_report(db: Session, period: Period) -> dict[str, Any]:
    by_method = (
        db.query(Payment.method, func.count(Payment.id),
                 func.coalesce(func.sum(Payment.amount_paise), 0))
        .filter(Payment.status == "confirmed", Payment.confirmed_at.isnot(None),
                func.date(Payment.confirmed_at) >= period.start,
                func.date(Payment.confirmed_at) <= period.end)
        .group_by(Payment.method)
        .all()
    )
    pending = (
        db.query(func.count(Payment.id), func.coalesce(func.sum(Payment.amount_paise), 0))
        .filter(Payment.status == "pending_confirmation")
        .one()
    )
    return {
        "by_method": [{"method": m, "count": int(c), "value_paise": int(v)}
                      for m, c, v in by_method],
        "total_paise": sum(int(v) for _, _, v in by_method),
        "pending_cash_confirmations": {"count": int(pending[0]),
                                       "value_paise": int(pending[1])},
    }


def order_status_mix(db: Session, period: Period) -> list[dict[str, Any]]:
    rows = (
        db.query(Order.status, func.count(Order.id),
                 func.coalesce(func.sum(Order.grand_total_paise), 0))
        .filter(Order.order_date >= period.start, Order.order_date <= period.end)
        .group_by(Order.status)
        .all()
    )
    return [{"status": s, "orders": int(c), "value_paise": int(v)} for s, c, v in rows]


def min_rule_impact(db: Session, period: Period) -> dict[str, Any]:
    """How much of the bill comes from the 11 sq.ft minimum (BR-SQFT-03).
    The owner asked for this rule; this is how it actually pays."""
    row = (
        db.query(
            func.count(OrderItem.id),
            func.sum(case((OrderItem.min_rule_applied.is_(True), 1), else_=0)),
            func.sum(case((OrderItem.min_rule_applied.is_(True),
                           (OrderItem.billable_sqft - OrderItem.raw_sqft)
                           * OrderItem.quantity), else_=0)),
        )
        .join(Order, Order.id == OrderItem.order_id)
        .filter(Order.status.in_(LIVE_STATES),
                Order.order_date >= period.start, Order.order_date <= period.end)
        .one()
    )
    return {"total_lines": int(row[0] or 0),
            "lines_with_min_rule": int(row[1] or 0),
            "extra_sqft_billed": float(row[2] or 0)}
