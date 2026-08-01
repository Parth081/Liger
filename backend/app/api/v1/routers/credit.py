"""Credit endpoints (API_SPEC §5)."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.deps import Actor, Pagination, get_actor, get_db, pagination, require
from app.core.exceptions import Forbidden
from app.modules.credit import service
from app.modules.credit.exposure import colour_state, compute_position
from app.modules.credit.ledger import statement
from app.modules.credit.models import CreditEvent, CustomerScore
from app.modules.customers.models import Customer

router = APIRouter(prefix="/credit", tags=["credit"])
customer_router = APIRouter(prefix="/customers", tags=["credit"])


def _position_payload(db: Session, customer: Customer, on: date) -> dict:
    p = compute_position(db, customer, on)
    return {
        "customer_uid": str(customer.uid),
        "business_name": customer.business_name,
        "status": customer.status,
        "colour": colour_state(p, customer.status),           # BR-CR-30…33
        "outstanding_paise": p.outstanding.paise,
        "uninvoiced_orders_paise": p.uninvoiced_orders.paise,
        "exposure_paise": p.exposure.paise,
        "base_limit_paise": p.base_limit.paise,
        "cash_bonus_paise": p.cash_bonus.paise,
        "override_extra_paise": p.override_extra.paise,
        "effective_limit_paise": p.effective_limit.paise,
        "available_paise": p.available.paise,
        "ageing": {b.label: b.amount.paise for b in p.buckets},
        "overdue_invoices": [
            {"invoice_no": o.invoice_no, "amount_paise": o.amount_paise,
             "due_date": str(o.due_date), "days_overdue": o.days_overdue}
            for o in p.overdue_invoices
        ],
    }


@router.get("/customers/{customer_uid}/status")
def credit_status(customer_uid: str, db: Session = Depends(get_db),
                  actor: Actor = Depends(get_actor)):
    customer = service.get_customer_by_uid(db, customer_uid)
    if actor.is_dealer and customer.id != actor.customer_id:
        raise Forbidden("Not your account")                    # BR-AC-07
    return _position_payload(db, customer, date.today())


@router.get("/blocked")
def blocked_customers(db: Session = Depends(get_db),
                      actor: Actor = Depends(require("credit.read"))):
    rows = (
        db.query(Customer)
        .filter(Customer.status == "blocked", Customer.deleted_at.is_(None))
        .all()
    )
    return {"items": [_position_payload(db, c, date.today()) for c in rows]}


@router.get("/ageing")
def ageing_report(page: Pagination = Depends(pagination), db: Session = Depends(get_db),
                  actor: Actor = Depends(require("credit.read"))):
    q = db.query(Customer).filter(Customer.deleted_at.is_(None))
    if page.cursor is not None:
        q = q.filter(Customer.id > page.cursor)
    rows = q.order_by(Customer.id).limit(page.limit + 1).all()
    next_cursor = str(rows[page.limit - 1].id) if len(rows) > page.limit else None
    return {"items": [_position_payload(db, c, date.today()) for c in rows[: page.limit]],
            "next_cursor": next_cursor}


class SimulateIn(BaseModel):
    customer_uid: str
    new_limit_paise: int = Field(ge=0)


@router.post("/simulate")
def simulate(body: SimulateIn, db: Session = Depends(get_db),
             actor: Actor = Depends(require("credit.simulate"))):
    customer = service.get_customer_by_uid(db, body.customer_uid)
    return service.simulate_limit(db, customer, body.new_limit_paise, date.today())


# ---------------- customer-scoped admin actions ----------------
class LimitIn(BaseModel):
    credit_limit_paise: int = Field(ge=0)
    reason: str = Field(min_length=3, max_length=255)


@customer_router.patch("/{customer_uid}/limit")
def change_limit(customer_uid: str, body: LimitIn, db: Session = Depends(get_db),
                 actor: Actor = Depends(require("customer.limit"))):
    customer = service.get_customer_by_uid(db, customer_uid)
    service.change_limit(db, customer, body.credit_limit_paise,
                         reason=body.reason, actor_id=actor.id)
    return {"customer_uid": str(customer.uid), "credit_limit_paise": customer.credit_limit_paise}


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=255)


@customer_router.post("/{customer_uid}/block")
def block(customer_uid: str, body: ReasonIn, db: Session = Depends(get_db),
          actor: Actor = Depends(require("customer.limit"))):
    customer = service.get_customer_by_uid(db, customer_uid)
    service.manual_block(db, customer, reason=body.reason, actor_id=actor.id)
    return {"customer_uid": str(customer.uid), "status": customer.status}


@customer_router.post("/{customer_uid}/unblock")
def unblock(customer_uid: str, body: ReasonIn, db: Session = Depends(get_db),
            actor: Actor = Depends(require("customer.limit"))):
    customer = service.get_customer_by_uid(db, customer_uid)
    service.manual_unblock(db, customer, reason=body.reason, actor_id=actor.id)
    return {"customer_uid": str(customer.uid), "status": customer.status}


class OverrideIn(BaseModel):
    extra_limit_paise: int = Field(gt=0)
    valid_until: date
    reason: str = Field(min_length=3, max_length=255)


@customer_router.post("/{customer_uid}/overrides", status_code=201)
def grant_override(customer_uid: str, body: OverrideIn, db: Session = Depends(get_db),
                   actor: Actor = Depends(require("customer.limit"))):
    customer = service.get_customer_by_uid(db, customer_uid)
    override = service.grant_override(db, customer, extra_limit_paise=body.extra_limit_paise,
                                      valid_until=body.valid_until, reason=body.reason,
                                      actor_id=actor.id)
    return {"uid": str(override.uid), "valid_until": str(override.valid_until)}


@customer_router.get("/{customer_uid}/credit-events")
def credit_events(customer_uid: str, shadow_only: bool = Query(False),
                  page: Pagination = Depends(pagination),
                  db: Session = Depends(get_db),
                  actor: Actor = Depends(require("credit.read"))):
    customer = service.get_customer_by_uid(db, customer_uid)
    q = db.query(CreditEvent).filter(CreditEvent.customer_id == customer.id)
    if shadow_only:
        q = q.filter(CreditEvent.is_shadow.is_(True))         # BR-CR-40 review screen
    if page.cursor is not None:
        q = q.filter(CreditEvent.id < page.cursor)
    rows = q.order_by(CreditEvent.id.desc()).limit(page.limit + 1).all()
    next_cursor = str(rows[page.limit - 1].id) if len(rows) > page.limit else None
    return {
        "items": [
            {"event_type": e.event_type, "reason": e.reason, "detail": e.detail,
             "is_shadow": e.is_shadow, "actor_type": e.actor_type,
             "at": e.created_at.isoformat()}
            for e in rows[: page.limit]
        ],
        "next_cursor": next_cursor,
    }


@customer_router.get("/{customer_uid}/score")
def customer_score(customer_uid: str, db: Session = Depends(get_db),
                   actor: Actor = Depends(require("credit.read"))):
    customer = service.get_customer_by_uid(db, customer_uid)
    row = (
        db.query(CustomerScore)
        .filter(CustomerScore.customer_id == customer.id)
        .order_by(CustomerScore.computed_on.desc())
        .first()
    )
    if row is None:
        return {"band": "NEW", "score": None, "factors": {}, "suggested_limit_paise": None}
    return {"band": row.band, "score": row.score, "factors": row.factors,
            "suggested_limit_paise": row.suggested_limit_paise,
            "computed_on": str(row.computed_on)}


@customer_router.get("/{customer_uid}/ledger")
def customer_ledger(customer_uid: str, page: Pagination = Depends(pagination),
                    db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
    customer = service.get_customer_by_uid(db, customer_uid)
    if actor.is_dealer and customer.id != actor.customer_id:
        raise Forbidden("Not your account")
    if not actor.is_dealer and actor.role not in ("super_admin", "admin", "accounts", "sales_rep"):
        raise Forbidden("No ledger access")                    # BR-AC-05
    entries = statement(db, customer.id, limit=page.limit, cursor=page.cursor)
    next_cursor = str(entries[-1].id) if len(entries) == page.limit else None
    return {
        "items": [
            {"entry_type": e.entry_type, "debit_paise": e.debit_paise,
             "credit_paise": e.credit_paise, "balance_after_paise": e.balance_after_paise,
             "narration": e.narration, "ref_type": e.ref_type,
             "posted_at": e.posted_at.isoformat()}
            for e in entries
        ],
        "next_cursor": next_cursor,
    }
