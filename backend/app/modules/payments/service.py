"""Payments — BR-PAY-01…10.

The two iron rules:
1. An online payment becomes real ONLY via a signature-verified, idempotent
   webhook (BR-PAY-03/04). The browser redirect is never trusted.
2. Cash frees ZERO credit until an admin confirms it (BR-PAY-05).
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.core import events, idempotency
from app.core.audit import write_audit
from app.core.exceptions import Conflict, NotFound, ValidationFailed
from app.core.money import Money
from app.db.base import utcnow
from app.modules.credit import gate as credit_gate
from app.modules.credit import ledger
from app.modules.credit.models import Invoice
from app.modules.credit.service import reevaluate_block_state
from app.modules.customers.models import Customer
from app.modules.payments.gateway import get_gateway
from app.modules.payments.models import (
    GatewayEvent,
    Payment,
    PaymentAllocation,
    PaymentLink,
)

_ONLINE_METHODS = ("upi", "card", "netbanking", "wallet")
_OFFLINE_METHODS = ("cash", "cheque", "bank_transfer")


# ---------------- allocation (BR-PAY-06) ----------------
def allocate_fifo(db: Session, payment: Payment) -> list[PaymentAllocation]:
    """Oldest-due-first. Partial allowed; remainder stays on account."""
    remaining = payment.amount_paise
    allocations: list[PaymentAllocation] = []
    invoices = (
        db.query(Invoice)
        .filter(Invoice.customer_id == payment.customer_id, Invoice.status == "open")
        .order_by(Invoice.due_date, Invoice.id)
        .all()
    )
    for invoice in invoices:
        if remaining <= 0:
            break
        gap = invoice.outstanding_paise
        if gap <= 0:
            continue
        take = min(remaining, gap)
        allocations.append(PaymentAllocation(payment_id=payment.id, invoice_id=invoice.id,
                                             amount_paise=take, created_at=utcnow()))
        invoice.amount_paid_paise += take
        if invoice.outstanding_paise <= 0:
            invoice.status = "paid"
        remaining -= take
    for a in allocations:
        db.add(a)
    db.flush()
    return allocations


def _post_confirmed_payment(db: Session, payment: Payment, *, actor_id: int | None) -> None:
    """Shared tail for webhook + cash confirmation: ledger, FIFO, unblock, event."""
    customer = credit_gate.lock_customer(db, payment.customer_id)
    if customer is None:
        raise NotFound("Customer not found")
    ledger.post_entry(
        db, customer=customer, entry_type="payment",
        credit=Money(payment.amount_paise),
        ref_type="payment", ref_id=payment.id,
        meta={"method": payment.method},           # feeds the cash-ratio bonus (BR-CR-06)
        narration=f"Payment {payment.method} ₹{payment.amount_paise / 100:.2f}",
        actor_id=actor_id,
    )
    allocate_fifo(db, payment)
    reevaluate_block_state(db, customer, date.today())     # BR-CR-47 auto-unblock
    db.commit()
    events.emit("payment.confirmed", {"payment_id": payment.id,
                                      "customer_id": payment.customer_id,
                                      "amount_paise": payment.amount_paise})


# ---------------- online (BR-PAY-01/03/04) ----------------
def initiate_online(db: Session, *, customer: Customer, amount_paise: int,
                    method: str, idempotency_key: str,
                    actor_type: str, actor_id: int) -> tuple[Payment | None, dict | None]:
    if method not in _ONLINE_METHODS:
        raise ValidationFailed(f"Method {method} is not an online method")
    if amount_paise <= 0:
        raise ValidationFailed("Amount must be positive")
    replay = idempotency.begin(db, idempotency_key, "POST /payments/online/initiate",
                               {"customer_id": customer.id, "amount_paise": amount_paise})
    if replay is not None:
        return None, replay

    gateway = get_gateway()
    gw_order = gateway.create_order(amount_paise=amount_paise,
                                    receipt=f"cust-{customer.code}",
                                    notes={"customer": customer.code})
    payment = Payment(customer_id=customer.id, amount_paise=amount_paise, method=method,
                      status="initiated", gateway=gw_order.gateway,
                      gateway_order_id=gw_order.gateway_order_id,
                      idempotency_key=idempotency_key)
    db.add(payment)
    db.flush()
    response = {"payment_uid": str(payment.uid), "checkout": gw_order.checkout_params}
    idempotency.store(db, idempotency_key, 201, response)
    db.commit()
    return payment, None


def process_webhook(db: Session, *, body: bytes, signature: str, payload: dict) -> dict:
    """BR-PAY-03/04: THE only path an online payment becomes real."""
    gateway = get_gateway()
    signature_valid = gateway.verify_webhook_signature(body, signature)

    event_id = str(payload.get("event_id") or payload.get("id") or "")
    if not event_id:
        raise ValidationFailed("Webhook missing event id")

    # store raw event; duplicate event_id -> no-op (idempotent, BR-PAY-04)
    existing = db.query(GatewayEvent).filter(GatewayEvent.event_id == event_id).first()
    if existing is not None:
        return {"status": "duplicate", "event_id": event_id}

    event = GatewayEvent(gateway=gateway.name, event_id=event_id,
                         event_type=str(payload.get("event", "unknown")),
                         payload=payload, signature_valid=signature_valid,
                         received_at=utcnow())
    db.add(event)
    db.flush()

    if not signature_valid:
        db.commit()                                   # keep the evidence
        raise ValidationFailed("Invalid webhook signature")

    if payload.get("event") != "payment.captured":
        event.processed_at = utcnow()
        db.commit()
        return {"status": "ignored", "event": payload.get("event")}

    gateway_order_id = payload.get("gateway_order_id") or (
        payload.get("payload", {}).get("payment", {}).get("entity", {}).get("order_id")
    )
    payment = (
        db.query(Payment)
        .filter(Payment.gateway_order_id == gateway_order_id)
        .with_for_update()
        .first()
    )
    if payment is None:
        db.commit()
        raise NotFound(f"No payment for gateway order {gateway_order_id}")
    if payment.status == "confirmed":                 # replay after processing
        event.processed_at = utcnow()
        db.commit()
        return {"status": "already-confirmed"}

    payment.status = "confirmed"
    payment.confirmed_at = utcnow()
    payment.gateway_payment_id = str(
        payload.get("gateway_payment_id")
        or payload.get("payload", {}).get("payment", {}).get("entity", {}).get("id", "")
    )
    event.processed_at = utcnow()
    _post_confirmed_payment(db, payment, actor_id=None)
    return {"status": "processed", "payment_uid": str(payment.uid)}


# ---------------- offline + the cash gate (BR-PAY-02/05) ----------------
def record_offline(db: Session, *, customer: Customer, amount_paise: int, method: str,
                   reference_no: str | None, slip_url: str | None, notes: str | None,
                   actor_id: int) -> Payment:
    if method not in _OFFLINE_METHODS:
        raise ValidationFailed(f"Method {method} is not an offline method")
    if amount_paise <= 0:
        raise ValidationFailed("Amount must be positive")
    payment = Payment(customer_id=customer.id, amount_paise=amount_paise, method=method,
                      status="pending_confirmation",       # frees ZERO credit (BR-PAY-05)
                      reference_no=reference_no, slip_url=slip_url, notes=notes,
                      recorded_by=actor_id)
    db.add(payment)
    write_audit(db, actor_type="user", actor_id=actor_id, action="payment.record",
                entity_type="payment", entity_id=str(payment.uid),
                after={"amount_paise": amount_paise, "method": method})
    db.commit()
    db.refresh(payment)
    events.emit("cash.pending_confirmation", {"payment_id": payment.id,
                                              "customer_id": customer.id,
                                              "amount_paise": amount_paise})
    return payment


def confirm_offline(db: Session, payment: Payment, *, actor_id: int) -> Payment:
    """THE cash gate. Only now does the ledger move and the block re-evaluate."""
    if payment.status != "pending_confirmation":
        raise Conflict(f"Payment is {payment.status}, not pending confirmation")
    payment.status = "confirmed"
    payment.confirmed_by = actor_id
    payment.confirmed_at = utcnow()
    write_audit(db, actor_type="user", actor_id=actor_id, action="payment.confirm",
                entity_type="payment", entity_id=str(payment.uid),
                after={"amount_paise": payment.amount_paise, "method": payment.method})
    _post_confirmed_payment(db, payment, actor_id=actor_id)
    db.refresh(payment)
    return payment


def reject_offline(db: Session, payment: Payment, *, reason: str, actor_id: int) -> Payment:
    if payment.status != "pending_confirmation":
        raise Conflict(f"Payment is {payment.status}, not pending confirmation")
    if not reason.strip():
        raise ValidationFailed("A reason is required to reject a payment")
    payment.status = "failed"
    payment.rejected_reason = reason.strip()
    write_audit(db, actor_type="user", actor_id=actor_id, action="payment.reject",
                entity_type="payment", entity_id=str(payment.uid), after={"reason": reason})
    db.commit()
    return payment


# ---------------- reversal (BR-PAY-09) ----------------
def reverse_payment(db: Session, payment: Payment, *, reason: str, actor_id: int) -> Payment:
    """Bounce/chargeback: reversing ledger entry, de-allocate, re-open invoices.
    The invoices re-age on their ORIGINAL due dates, so the ladder resumes at
    the correct step automatically (EscalationState rows already fired stay)."""
    if payment.status != "confirmed":
        raise Conflict("Only a confirmed payment can be reversed")
    if not reason.strip():
        raise ValidationFailed("A reason is required to reverse a payment")

    customer = credit_gate.lock_customer(db, payment.customer_id)
    if customer is None:
        raise NotFound("Customer not found")

    for allocation in payment.allocations:
        invoice = db.get(Invoice, allocation.invoice_id)
        if invoice is not None:
            invoice.amount_paid_paise -= allocation.amount_paise
            if invoice.outstanding_paise > 0 and invoice.status == "paid":
                invoice.status = "open"
        db.delete(allocation)

    payment.status = "reversed"
    payment.reversed_at = utcnow()
    payment.reversal_reason = reason.strip()

    ledger.post_entry(db, customer=customer, entry_type="reversal",
                      debit=Money(payment.amount_paise),
                      ref_type="payment", ref_id=payment.id,
                      meta={"method": payment.method, "reversal": True},
                      narration=f"Reversal: {reason.strip()}",
                      actor_id=actor_id)
    write_audit(db, actor_type="user", actor_id=actor_id, action="payment.reverse",
                entity_type="payment", entity_id=str(payment.uid), after={"reason": reason})
    db.commit()
    return payment


# ---------------- manual allocation (BR-PAY-06) ----------------
def reallocate(db: Session, payment: Payment, targets: list[dict], *, actor_id: int) -> None:
    """targets: [{invoice_uid, amount_paise}] — replaces existing allocations."""
    import uuid as uuid_mod

    if payment.status != "confirmed":
        raise Conflict("Only a confirmed payment can be re-allocated")
    total = sum(t["amount_paise"] for t in targets)
    if total > payment.amount_paise:
        raise ValidationFailed("Allocations exceed the payment amount")

    for allocation in list(payment.allocations):
        invoice = db.get(Invoice, allocation.invoice_id)
        if invoice is not None:
            invoice.amount_paid_paise -= allocation.amount_paise
            if invoice.outstanding_paise > 0 and invoice.status == "paid":
                invoice.status = "open"
        db.delete(allocation)
    db.flush()

    for target in targets:
        invoice = db.query(Invoice).filter(
            Invoice.uid == uuid_mod.UUID(target["invoice_uid"])).first()
        if invoice is None or invoice.customer_id != payment.customer_id:
            raise NotFound("Invoice not found for this customer")
        if target["amount_paise"] <= 0:
            raise ValidationFailed("Allocation must be positive")
        if target["amount_paise"] > invoice.outstanding_paise:
            raise ValidationFailed(f"Allocation exceeds outstanding on {invoice.invoice_no}")
        db.add(PaymentAllocation(payment_id=payment.id, invoice_id=invoice.id,
                                 amount_paise=target["amount_paise"], created_at=utcnow()))
        invoice.amount_paid_paise += target["amount_paise"]
        if invoice.outstanding_paise <= 0:
            invoice.status = "paid"

    write_audit(db, actor_type="user", actor_id=actor_id, action="payment.reallocate",
                entity_type="payment", entity_id=str(payment.uid),
                after={"targets": [{"invoice": t["invoice_uid"],
                                    "amount_paise": t["amount_paise"]} for t in targets]})
    customer = db.get(Customer, payment.customer_id)
    if customer is not None:
        reevaluate_block_state(db, customer, date.today())
    db.commit()


# ---------------- payment links (BR-PAY-10) ----------------
def create_payment_link(db: Session, *, customer: Customer, invoice: Invoice | None,
                        amount_paise: int, actor_id: int) -> PaymentLink:
    if amount_paise <= 0:
        raise ValidationFailed("Amount must be positive")
    gateway = get_gateway()
    reference = invoice.invoice_no if invoice else f"onacct-{customer.code}-{utcnow():%Y%m%d%H%M}"
    link_id, url = gateway.create_payment_link(
        amount_paise=amount_paise, reference=reference,
        description=f"Liger payment — {reference}", expires_seconds=7 * 24 * 3600,
    )
    link = PaymentLink(customer_id=customer.id, invoice_id=invoice.id if invoice else None,
                       amount_paise=amount_paise, url=url, gateway_link_id=link_id,
                       expires_at=utcnow() + timedelta(days=7))
    db.add(link)
    db.commit()
    db.refresh(link)
    return link
