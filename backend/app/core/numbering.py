"""Gapless document numbering (BR-ORD-06, BR-TAX-05).

Allocated with a row lock INSIDE the caller's transaction, so a rollback
rolls the number back too — no gaps, no duplicates.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.modules.admin.models import NumberSeries


def financial_year(today: date) -> str:
    """Indian FY: 1 April – 31 March. 2026-08-01 -> '2026-27'."""
    if today.month >= 4:
        start = today.year
    else:
        start = today.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def next_number(db: Session, series: str) -> int:
    """Lock the series row (R7-style) and increment. Caller owns the transaction."""
    row = (
        db.query(NumberSeries)
        .filter(NumberSeries.series == series)
        .with_for_update()
        .first()
    )
    if row is None:
        row = NumberSeries(series=series, last_value=0)
        db.add(row)
        db.flush()
        # Re-lock after insert so concurrent creators serialize on the row.
        row = (
            db.query(NumberSeries)
            .filter(NumberSeries.series == series)
            .with_for_update()
            .one()
        )
    row.last_value += 1
    db.flush()
    return row.last_value


def next_order_no(db: Session, today: date) -> str:
    fy = financial_year(today)
    n = next_number(db, f"order/{fy}")
    return f"LGR/{fy}/{n:05d}"


def next_invoice_no(db: Session, today: date) -> str:
    fy = financial_year(today)
    n = next_number(db, f"invoice/{fy}")
    return f"LGR/INV/{fy}/{n:05d}"


def next_credit_note_no(db: Session, today: date) -> str:
    fy = financial_year(today)
    n = next_number(db, f"credit_note/{fy}")
    return f"LGR/CN/{fy}/{n:05d}"
