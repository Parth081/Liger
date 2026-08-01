"""Credit administration: block/unblock, limits, overrides, ladder, snapshots,
auto-unblock on payment (BR-CR-41…54)."""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.core import settings_registry
from app.core.audit import write_audit
from app.core.exceptions import NotFound, ValidationFailed
from app.db.base import utcnow
from app.modules.credit.exposure import colour_state, compute_position
from app.modules.credit.models import (
    CreditEvent,
    CreditOverride,
    CreditSnapshot,
    EscalationState,
    Invoice,
)
from app.modules.customers.models import Customer

LADDER_STEPS = ("pre_due", "due_today", "warn1", "warn2", "block")


def _event(db: Session, customer_id: int, event_type: str, *, reason: str | None = None,
           detail: dict | None = None, actor_type: str = "system",
           actor_id: int | None = None, is_shadow: bool = False) -> None:
    db.add(CreditEvent(customer_id=customer_id, event_type=event_type, reason=reason,
                       detail=detail, actor_type=actor_type, actor_id=actor_id,
                       is_shadow=is_shadow, created_at=utcnow()))


# ---------------- admin actions (BR-CR-50…52) ----------------
def change_limit(db: Session, customer: Customer, new_limit_paise: int, *,
                 reason: str, actor_id: int) -> Customer:
    if not reason.strip():
        raise ValidationFailed("A reason is required to change a credit limit")
    if new_limit_paise < 0:
        raise ValidationFailed("Limit cannot be negative")
    old = customer.credit_limit_paise
    customer.credit_limit_paise = new_limit_paise
    _event(db, customer.id, "limit_changed", reason=reason.strip(),
           detail={"old_paise": old, "new_paise": new_limit_paise},
           actor_type="user", actor_id=actor_id)
    write_audit(db, actor_type="user", actor_id=actor_id, action="customer.limit",
                entity_type="customer", entity_id=customer.code,
                before={"credit_limit_paise": old},
                after={"credit_limit_paise": new_limit_paise, "reason": reason})
    db.commit()
    return customer


def manual_block(db: Session, customer: Customer, *, reason: str, actor_id: int) -> Customer:
    if not reason.strip():
        raise ValidationFailed("A reason is required to block a customer")
    customer.status = "blocked"
    customer.is_manual_block = True          # BR-CR-52: outranks automatic logic
    customer.blocked_at = utcnow()
    customer.block_reason = reason.strip()
    _event(db, customer.id, "blocked", reason=reason.strip(),
           actor_type="user", actor_id=actor_id)
    write_audit(db, actor_type="user", actor_id=actor_id, action="customer.block",
                entity_type="customer", entity_id=customer.code, after={"reason": reason})
    db.commit()
    return customer


def manual_unblock(db: Session, customer: Customer, *, reason: str, actor_id: int) -> Customer:
    if not reason.strip():
        raise ValidationFailed("A reason is required to unblock a customer")
    customer.status = "active"
    customer.is_manual_block = False
    customer.unblocked_at = utcnow()
    customer.block_reason = None
    _event(db, customer.id, "unblocked", reason=reason.strip(),
           actor_type="user", actor_id=actor_id)
    write_audit(db, actor_type="user", actor_id=actor_id, action="customer.unblock",
                entity_type="customer", entity_id=customer.code, after={"reason": reason})
    db.commit()
    return customer


def grant_override(db: Session, customer: Customer, *, extra_limit_paise: int,
                   valid_until: date, reason: str, actor_id: int) -> CreditOverride:
    if not reason.strip():
        raise ValidationFailed("A reason is required for a credit override")
    if extra_limit_paise <= 0:
        raise ValidationFailed("Override amount must be positive")
    if valid_until <= date.today():
        raise ValidationFailed("Override expiry must be in the future")
    override = CreditOverride(customer_id=customer.id, extra_limit_paise=extra_limit_paise,
                              reason=reason.strip(), valid_until=valid_until,
                              granted_by=actor_id)
    db.add(override)
    _event(db, customer.id, "override_granted", reason=reason.strip(),
           detail={"extra_limit_paise": extra_limit_paise, "valid_until": str(valid_until)},
           actor_type="user", actor_id=actor_id)
    write_audit(db, actor_type="user", actor_id=actor_id, action="credit.override",
                entity_type="customer", entity_id=customer.code,
                after={"extra_limit_paise": extra_limit_paise, "valid_until": str(valid_until)})
    db.commit()
    db.refresh(override)
    return override


# ---------------- ladder (BR-CR-41…49) ----------------
def _ladder_offsets(db: Session) -> dict[str, int]:
    return {
        "pre_due": settings_registry.get_int(db, "ladder_pre_due"),   # −3
        "due_today": 0,
        "warn1": settings_registry.get_int(db, "ladder_warn1"),       # +3
        "warn2": settings_registry.get_int(db, "ladder_warn2"),       # +10
        "block": settings_registry.get_int(db, "ladder_block"),       # +15
    }


def advance_ladder(db: Session, on: date) -> dict[str, int]:
    """Nightly job (BR-CR-54). Per invoice, fire each due step at most once
    (BR-CR-49) — idempotent, safe to re-run. Returns counts per step."""
    offsets = _ladder_offsets(db)
    enforce = settings_registry.get_str(db, "credit_enforcement_mode") == "enforce"
    counts = {step: 0 for step in LADDER_STEPS}

    invoices = (
        db.query(Invoice)
        .filter(Invoice.status == "open")
        .all()
    )
    fired_already = {
        (row.invoice_id, row.step)
        for row in db.query(EscalationState.invoice_id, EscalationState.step).all()
    }

    to_block: set[int] = set()
    for inv in invoices:
        if inv.outstanding_paise <= 0:
            continue
        days_past_due = (on - inv.due_date).days
        for step, offset in offsets.items():
            if days_past_due < offset:
                continue
            if (inv.id, step) in fired_already:
                continue
            db.add(EscalationState(invoice_id=inv.id, customer_id=inv.customer_id,
                                   step=step, fired_on=on))
            _event(db, inv.customer_id, "ladder_step", is_shadow=not enforce,
                   detail={"invoice_no": inv.invoice_no, "step": step,
                           "days_past_due": days_past_due,
                           "outstanding_paise": inv.outstanding_paise})
            counts[step] += 1
            if step == "block":
                to_block.add(inv.customer_id)

    # BR-CR-45 auto-block — only when enforcement is on (BR-CR-40)
    if enforce:
        for customer_id in to_block:
            customer = db.get(Customer, customer_id)
            if customer is not None and customer.status != "blocked":
                customer.status = "blocked"
                customer.is_manual_block = False
                customer.blocked_at = utcnow()
                customer.block_reason = "Auto-block: invoice overdue beyond ladder limit"
                _event(db, customer_id, "blocked", reason="auto: ladder block step")
    db.commit()
    return counts


def reevaluate_block_state(db: Session, customer: Customer, on: date) -> Customer:
    """BR-CR-47: called on payment confirmation — auto-unblock within seconds
    when conditions clear. Manual blocks stay (BR-CR-52)."""
    if customer.status != "blocked" or customer.is_manual_block:
        return customer
    block_day = settings_registry.get_int(db, "ladder_block")
    position = compute_position(db, customer, on)
    still_blockworthy = any(o.days_overdue >= block_day for o in position.overdue_invoices)
    if not still_blockworthy:
        customer.status = "active"
        customer.unblocked_at = utcnow()
        customer.block_reason = None
        _event(db, customer.id, "unblocked", reason="auto: dues cleared")
        db.commit()
    return customer


# ---------------- snapshots (BR-CR-54) ----------------
def write_snapshot(db: Session, customer: Customer, on: date) -> CreditSnapshot:
    position = compute_position(db, customer, on)
    colour = colour_state(position, customer.status)
    bucket = {b.label: b.amount.paise for b in position.buckets}
    existing = (
        db.query(CreditSnapshot)
        .filter(CreditSnapshot.customer_id == customer.id, CreditSnapshot.as_of == on)
        .first()
    )
    if existing is not None:
        db.delete(existing)      # idempotent re-run: replace the day's snapshot
        db.flush()
    snap = CreditSnapshot(
        customer_id=customer.id, as_of=on,
        outstanding_paise=position.outstanding.paise,
        exposure_paise=position.exposure.paise,
        effective_limit_paise=position.effective_limit.paise,
        available_paise=position.available.paise,
        overdue_current_paise=bucket.get("current", 0),
        overdue_1_30_paise=bucket.get("1-30", 0),
        overdue_31_60_paise=bucket.get("31-60", 0),
        overdue_61_90_paise=bucket.get("61-90", 0),
        overdue_90_plus_paise=bucket.get("90+", 0),
        colour=colour, status=customer.status,
    )
    db.add(snap)
    db.commit()
    return snap


def nightly_credit_run(db: Session, on: date) -> dict:
    """The 00:30/01:00 job pair, callable as one unit: re-age, advance ladder,
    expire overrides, write snapshots. Idempotent."""
    expired = (
        db.query(CreditOverride)
        .filter(CreditOverride.valid_until < on, CreditOverride.revoked_at.is_(None))
        .all()
    )
    for override in expired:
        override.revoked_at = utcnow()      # BR-CR-50 auto-revert
        _event(db, override.customer_id, "override_expired",
               detail={"extra_limit_paise": override.extra_limit_paise})
    counts = advance_ladder(db, on)
    customers = db.query(Customer).filter(Customer.deleted_at.is_(None)).all()
    for customer in customers:
        write_snapshot(db, customer, on)
    db.commit()
    return {"ladder": counts, "overrides_expired": len(expired), "snapshots": len(customers)}


# ---------------- simulation (BR-CR-53) ----------------
def simulate_limit(db: Session, customer: Customer, new_limit_paise: int, on: date) -> dict:
    """What-if — computes both positions, commits nothing."""
    current = compute_position(db, customer, on)
    old_limit = customer.credit_limit_paise
    try:
        customer.credit_limit_paise = new_limit_paise
        proposed = compute_position(db, customer, on)
    finally:
        customer.credit_limit_paise = old_limit
    db.rollback()
    return {
        "current": {"effective_limit_paise": current.effective_limit.paise,
                    "available_paise": current.available.paise,
                    "colour": colour_state(current, customer.status)},
        "proposed": {"effective_limit_paise": proposed.effective_limit.paise,
                     "available_paise": proposed.available.paise,
                     "colour": colour_state(proposed, customer.status)},
    }


def get_customer_by_uid(db: Session, customer_uid: str) -> Customer:
    import uuid as uuid_mod

    customer = db.query(Customer).filter(Customer.uid == uuid_mod.UUID(customer_uid)).first()
    if customer is None:
        raise NotFound("Customer not found")
    return customer
