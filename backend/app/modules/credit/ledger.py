"""Append-only ledger service (R2/R3, BR-LED-01…05).

Every posting computes balance_after inside the caller's transaction with the
customer row already locked (callers go through post_entry). Corrections are
reversing entries — there is no update path, and the DB triggers (migration
0004) reject UPDATE/DELETE even from raw SQL.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.exceptions import ValidationFailed
from app.core.money import Money
from app.db.base import utcnow
from app.modules.credit.models import LedgerEntry
from app.modules.customers.models import Customer


def current_balance(db: Session, customer_id: int) -> Money:
    """BR-CR-01: outstanding derived from the ledger. Opening balances are
    posted as `opening` entries (BR-LED-05), so the ledger is complete."""
    last = (
        db.query(LedgerEntry.balance_after_paise)
        .filter(LedgerEntry.customer_id == customer_id)
        .order_by(LedgerEntry.id.desc())
        .first()
    )
    return Money(last[0]) if last else Money.zero()


def derived_balance(db: Session, customer_id: int) -> Money:
    """Σ(debits) − Σ(credits) — used by the reconciliation assertion (BR-LED-04)."""
    row = (
        db.query(
            func.coalesce(func.sum(LedgerEntry.debit_paise), 0),
            func.coalesce(func.sum(LedgerEntry.credit_paise), 0),
        )
        .filter(LedgerEntry.customer_id == customer_id)
        .one()
    )
    return Money(int(row[0]) - int(row[1]))


def post_entry(
    db: Session,
    *,
    customer: Customer,
    entry_type: str,
    debit: Money = Money.zero(),
    credit: Money = Money.zero(),
    ref_type: str | None = None,
    ref_id: int | None = None,
    meta: dict[str, Any] | None = None,
    narration: str | None = None,
    actor_id: int | None = None,
) -> LedgerEntry:
    """Append one entry. Caller holds the customer row lock and owns the txn."""
    if debit.paise < 0 or credit.paise < 0:
        raise ValidationFailed("Ledger amounts must be non-negative; use a reversal entry")
    if (debit.paise == 0) == (credit.paise == 0):
        # A zero `opening` entry is the exception: it records "this customer
        # started clean" during migration, which the audit trail needs and
        # which makes a re-run of the importer a no-op (BR-LED-05).
        if not (entry_type == "opening" and debit.paise == 0 and credit.paise == 0):
            raise ValidationFailed("Exactly one of debit/credit must be non-zero")

    balance = current_balance(db, customer.id) + debit - credit
    entry = LedgerEntry(
        customer_id=customer.id,
        entry_type=entry_type,
        debit_paise=debit.paise,
        credit_paise=credit.paise,
        balance_after_paise=balance.paise,
        ref_type=ref_type,
        ref_id=ref_id,
        meta=meta,
        narration=narration,
        posted_at=utcnow(),
        created_by=actor_id,
    )
    db.add(entry)
    db.flush()
    return entry


def post_opening_balance(db: Session, customer: Customer, amount: Money,
                         actor_id: int | None) -> LedgerEntry:
    """BR-LED-05 — one opening entry per customer, from the migrated books."""
    existing = (
        db.query(LedgerEntry)
        .filter(LedgerEntry.customer_id == customer.id, LedgerEntry.entry_type == "opening")
        .first()
    )
    if existing is not None:
        raise ValidationFailed("Opening balance already posted for this customer")
    if amount.paise == 0:
        return post_entry(db, customer=customer, entry_type="opening",
                          narration="Opening balance nil (migrated)", actor_id=actor_id)
    if amount.paise > 0:
        return post_entry(db, customer=customer, entry_type="opening", debit=amount,
                          narration="Opening balance (migrated)", actor_id=actor_id)
    return post_entry(db, customer=customer, entry_type="opening", credit=-amount,
                      narration="Opening balance (migrated, in credit)", actor_id=actor_id)


def statement(db: Session, customer_id: int, *, limit: int = 500,
              cursor: int | None = None) -> list[LedgerEntry]:
    """BR-LED-03: reconstructable purely from entries. Cursor-paginated (R13)."""
    q = db.query(LedgerEntry).filter(LedgerEntry.customer_id == customer_id)
    if cursor is not None:
        q = q.filter(LedgerEntry.id > cursor)
    return q.order_by(LedgerEntry.id).limit(limit).all()
