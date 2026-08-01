"""Payments + invoices endpoints (API_SPEC §6)."""
from __future__ import annotations

import uuid as uuid_mod
from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.deps import (
    Actor,
    Pagination,
    get_actor,
    get_db,
    idempotency_key,
    pagination,
    require,
)
from app.core.exceptions import Forbidden, NotFound
from app.core.money import Money
from app.modules.credit.models import Invoice
from app.modules.customers.models import Customer
from app.modules.invoicing import service as invoicing
from app.modules.orders.models import Order
from app.modules.payments import service
from app.modules.payments.models import Payment

router = APIRouter(tags=["payments"])


def _customer_by_uid(db: Session, customer_uid: str) -> Customer:
    customer = db.query(Customer).filter(Customer.uid == uuid_mod.UUID(customer_uid)).first()
    if customer is None:
        raise NotFound("Customer not found")
    return customer


def _payment_payload(p: Payment) -> dict:
    return {
        "uid": str(p.uid), "amount_paise": p.amount_paise, "method": p.method,
        "status": p.status, "reference_no": p.reference_no,
        "gateway_order_id": p.gateway_order_id,
        "confirmed_at": p.confirmed_at.isoformat() if p.confirmed_at else None,
        "allocations": [
            {"invoice_id": a.invoice_id, "amount_paise": a.amount_paise}
            for a in p.allocations
        ],
        "created_at": p.created_at.isoformat(),
    }


# ---------------- online ----------------
class OnlineInitiateIn(BaseModel):
    customer_uid: str | None = None       # staff on behalf; dealers use token scope
    amount_paise: int = Field(gt=0)
    method: str = Field(pattern="^(upi|card|netbanking|wallet)$")


@router.post("/payments/online/initiate", status_code=201)
def initiate_online(body: OnlineInitiateIn, db: Session = Depends(get_db),
                    actor: Actor = Depends(get_actor),
                    idem_key: str = Depends(idempotency_key)):
    if actor.is_dealer:
        if actor.customer_id is None:
            raise Forbidden("Login not linked to a customer")
        customer = db.get(Customer, actor.customer_id)
    else:
        if not body.customer_uid:
            raise NotFound("customer_uid required for staff-initiated payments")
        customer = _customer_by_uid(db, body.customer_uid)
    assert customer is not None
    payment, replay = service.initiate_online(
        db, customer=customer, amount_paise=body.amount_paise, method=body.method,
        idempotency_key=idem_key, actor_type=actor.type, actor_id=actor.id,
    )
    if replay is not None:
        return replay["body"]
    assert payment is not None
    return {"payment_uid": str(payment.uid),
            "checkout": {"order_id": payment.gateway_order_id,
                         "amount": payment.amount_paise}}


@router.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    """Public + signature-verified. THE only path an online payment posts
    to the ledger (BR-PAY-03). Idempotent on event id (BR-PAY-04)."""
    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    import json

    payload = json.loads(body or b"{}")
    return service.process_webhook(db, body=body, signature=signature, payload=payload)


# ---------------- offline + cash gate ----------------
class OfflineIn(BaseModel):
    customer_uid: str
    amount_paise: int = Field(gt=0)
    method: str = Field(pattern="^(cash|cheque|bank_transfer)$")
    reference_no: str | None = Field(default=None, max_length=80)
    slip_url: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=255)


@router.post("/payments/offline", status_code=201)
def record_offline(body: OfflineIn, db: Session = Depends(get_db),
                   actor: Actor = Depends(require("payment.record"))):
    customer = _customer_by_uid(db, body.customer_uid)
    payment = service.record_offline(db, customer=customer, amount_paise=body.amount_paise,
                                     method=body.method, reference_no=body.reference_no,
                                     slip_url=body.slip_url, notes=body.notes,
                                     actor_id=actor.id)
    return _payment_payload(payment)


def _payment_by_uid(db: Session, payment_uid: str) -> Payment:
    payment = db.query(Payment).filter(Payment.uid == uuid_mod.UUID(payment_uid)).first()
    if payment is None:
        raise NotFound("Payment not found")
    return payment


@router.post("/payments/{payment_uid}/confirm")
def confirm_payment(payment_uid: str, db: Session = Depends(get_db),
                    actor: Actor = Depends(require("payment.confirm_cash"))):
    """The cash gate (BR-PAY-05) — admin says 'cash received', credit frees."""
    payment = _payment_by_uid(db, payment_uid)
    service.confirm_offline(db, payment, actor_id=actor.id)
    return _payment_payload(payment)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=255)


@router.post("/payments/{payment_uid}/reject")
def reject_payment(payment_uid: str, body: ReasonIn, db: Session = Depends(get_db),
                   actor: Actor = Depends(require("payment.confirm_cash"))):
    payment = _payment_by_uid(db, payment_uid)
    service.reject_offline(db, payment, reason=body.reason, actor_id=actor.id)
    return _payment_payload(payment)


@router.post("/payments/{payment_uid}/reverse")
def reverse_payment(payment_uid: str, body: ReasonIn, db: Session = Depends(get_db),
                    actor: Actor = Depends(require("payment.reverse"))):
    payment = _payment_by_uid(db, payment_uid)
    service.reverse_payment(db, payment, reason=body.reason, actor_id=actor.id)
    return _payment_payload(payment)


class AllocationIn(BaseModel):
    targets: list[dict] = Field(min_length=1)   # [{invoice_uid, amount_paise}]


@router.post("/payments/{payment_uid}/allocations")
def reallocate(payment_uid: str, body: AllocationIn, db: Session = Depends(get_db),
               actor: Actor = Depends(require("payment.confirm_cash"))):
    payment = _payment_by_uid(db, payment_uid)
    service.reallocate(db, payment, body.targets, actor_id=actor.id)
    return _payment_payload(payment)


@router.get("/payments")
def list_payments(status: str | None = Query(None), customer_uid: str | None = Query(None),
                  page: Pagination = Depends(pagination), db: Session = Depends(get_db),
                  actor: Actor = Depends(get_actor)):
    q = db.query(Payment)
    if actor.is_dealer:
        q = q.filter(Payment.customer_id == actor.customer_id)   # BR-AC-07
    elif customer_uid:
        q = q.filter(Payment.customer_id == _customer_by_uid(db, customer_uid).id)
    elif actor.role not in ("super_admin", "admin", "accounts", "sales_rep"):
        raise Forbidden("No payment visibility")                  # BR-AC-05
    if status:
        q = q.filter(Payment.status == status)
    if page.cursor is not None:
        q = q.filter(Payment.id < page.cursor)
    rows = q.order_by(Payment.id.desc()).limit(page.limit + 1).all()
    next_cursor = str(rows[page.limit - 1].id) if len(rows) > page.limit else None
    return {"items": [_payment_payload(p) for p in rows[: page.limit]],
            "next_cursor": next_cursor}


# ---------------- payment links ----------------
class LinkIn(BaseModel):
    customer_uid: str
    invoice_uid: str | None = None
    amount_paise: int = Field(gt=0)


@router.post("/payment-links", status_code=201)
def create_link(body: LinkIn, db: Session = Depends(get_db),
                actor: Actor = Depends(require("payment.record"))):
    customer = _customer_by_uid(db, body.customer_uid)
    invoice = None
    if body.invoice_uid:
        invoice = db.query(Invoice).filter(Invoice.uid == uuid_mod.UUID(body.invoice_uid)).first()
        if invoice is None:
            raise NotFound("Invoice not found")
    link = service.create_payment_link(db, customer=customer, invoice=invoice,
                                       amount_paise=body.amount_paise, actor_id=actor.id)
    return {"uid": str(link.uid), "url": link.url, "expires_at": link.expires_at.isoformat()}


# ---------------- invoices ----------------
class InvoiceCreateIn(BaseModel):
    order_uid: str


@router.post("/invoices", status_code=201)
def create_invoice(body: InvoiceCreateIn, db: Session = Depends(get_db),
                   actor: Actor = Depends(require("invoice.write"))):
    order = db.query(Order).filter(Order.uid == uuid_mod.UUID(body.order_uid)).first()
    if order is None:
        raise NotFound("Order not found")
    invoice = invoicing.create_invoice_from_order(db, order, on=date.today(),
                                                  actor_id=actor.id)
    return {"uid": str(invoice.uid), "invoice_no": invoice.invoice_no,
            "due_date": str(invoice.due_date), "total_paise": invoice.total_paise}


@router.get("/invoices")
def list_invoices(status: str | None = Query(None), overdue: bool = Query(False),
                  customer_uid: str | None = Query(None),
                  page: Pagination = Depends(pagination), db: Session = Depends(get_db),
                  actor: Actor = Depends(get_actor)):
    q = db.query(Invoice)
    if actor.is_dealer:
        q = q.filter(Invoice.customer_id == actor.customer_id)
    elif customer_uid:
        q = q.filter(Invoice.customer_id == _customer_by_uid(db, customer_uid).id)
    elif actor.role not in ("super_admin", "admin", "accounts", "sales_rep"):
        raise Forbidden("No invoice visibility")
    if status:
        q = q.filter(Invoice.status == status)
    if overdue:
        q = q.filter(Invoice.status == "open", Invoice.due_date < date.today())
    if page.cursor is not None:
        q = q.filter(Invoice.id < page.cursor)
    rows = q.order_by(Invoice.id.desc()).limit(page.limit + 1).all()
    next_cursor = str(rows[page.limit - 1].id) if len(rows) > page.limit else None
    return {
        "items": [
            {"uid": str(i.uid), "invoice_no": i.invoice_no, "total_paise": i.total_paise,
             "outstanding_paise": i.outstanding_paise, "due_date": str(i.due_date),
             "status": i.status,
             "days_overdue": max(0, (date.today() - i.due_date).days) if i.status == "open" else 0}
            for i in rows[: page.limit]
        ],
        "next_cursor": next_cursor,
    }


class CreditNoteIn(BaseModel):
    invoice_uid: str
    amount_paise: int = Field(gt=0)
    reason: str = Field(min_length=3, max_length=255)


@router.post("/credit-notes", status_code=201)
def create_credit_note(body: CreditNoteIn, db: Session = Depends(get_db),
                       actor: Actor = Depends(require("invoice.write"))):
    invoice = db.query(Invoice).filter(Invoice.uid == uuid_mod.UUID(body.invoice_uid)).first()
    if invoice is None:
        raise NotFound("Invoice not found")
    note = invoicing.create_credit_note(db, invoice, amount=Money(body.amount_paise),
                                        reason=body.reason, on=date.today(),
                                        actor_id=actor.id)
    return {"uid": str(note.uid), "credit_note_no": note.credit_note_no}
