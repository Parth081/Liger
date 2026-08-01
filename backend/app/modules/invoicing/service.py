"""Invoicing — BR-TAX-05/06, BR-CR-03. Invoices post to the ledger; credit
notes are the only reduction path."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.exceptions import Conflict, NotFound, ValidationFailed
from app.core.money import Money
from app.core.numbering import next_credit_note_no, next_invoice_no
from app.modules.credit import gate as credit_gate
from app.modules.credit import ledger
from app.modules.credit.models import Invoice
from app.modules.orders.models import Order
from app.modules.payments.models import CreditNote


def create_invoice_from_order(db: Session, order: Order, *, on: date,
                              actor_id: int | None) -> Invoice:
    """Freeze the order's totals into an invoice, set due_date from the
    customer's credit days (BR-CR-03), allocate a gapless number in-transaction
    (BR-TAX-05), and post the debit to the ledger (BR-LED-02)."""
    if order.status not in ("DELIVERED", "PARTIALLY_DELIVERED", "DISPATCHED",
                            "READY", "IN_PRODUCTION", "CONFIRMED"):
        raise ValidationFailed(f"Cannot invoice an order in status {order.status}")
    existing = db.query(Invoice).filter(Invoice.order_id == order.id,
                                        Invoice.status != "cancelled").first()
    if existing is not None:
        raise Conflict(f"Order {order.order_no} is already invoiced ({existing.invoice_no})")

    customer = credit_gate.lock_customer(db, order.customer_id)
    if customer is None:
        raise NotFound("Customer not found")

    invoice = Invoice(
        invoice_no=next_invoice_no(db, on),
        customer_id=order.customer_id,
        order_id=order.id,
        invoice_date=on,
        due_date=on + timedelta(days=customer.credit_days),
        total_paise=order.grand_total_paise,
        status="open",
    )
    db.add(invoice)
    db.flush()

    ledger.post_entry(db, customer=customer, entry_type="invoice",
                      debit=Money(order.grand_total_paise),
                      ref_type="invoice", ref_id=invoice.id,
                      narration=f"Invoice {invoice.invoice_no} for order {order.order_no}",
                      actor_id=actor_id)
    write_audit(db, actor_type="user" if actor_id else "system", actor_id=actor_id,
                action="invoice.create", entity_type="invoice", entity_id=invoice.invoice_no,
                after={"total_paise": invoice.total_paise, "due_date": str(invoice.due_date)})
    db.commit()
    db.refresh(invoice)
    return invoice


def create_credit_note(db: Session, invoice: Invoice, *, amount: Money, reason: str,
                       on: date, actor_id: int) -> CreditNote:
    """BR-TAX-06: never edit an invoice — reduce it with a credit note that
    posts a ledger credit."""
    if not reason.strip():
        raise ValidationFailed("A reason is required for a credit note")
    if amount.paise <= 0:
        raise ValidationFailed("Credit note amount must be positive")
    if amount.paise > invoice.outstanding_paise:
        raise ValidationFailed(
            "Credit note exceeds the invoice outstanding",
            {"outstanding_paise": invoice.outstanding_paise},
        )
    customer = credit_gate.lock_customer(db, invoice.customer_id)
    if customer is None:
        raise NotFound("Customer not found")

    note = CreditNote(
        credit_note_no=next_credit_note_no(db, on),
        customer_id=invoice.customer_id,
        invoice_id=invoice.id,
        amount_paise=amount.paise,
        reason=reason.strip(),
        created_by=actor_id,
    )
    db.add(note)
    db.flush()

    # the note reduces what is owed on this invoice
    invoice.amount_paid_paise += amount.paise
    if invoice.outstanding_paise <= 0:
        invoice.status = "paid"

    ledger.post_entry(db, customer=customer, entry_type="credit_note",
                      credit=amount, ref_type="credit_note", ref_id=note.id,
                      narration=f"Credit note {note.credit_note_no} vs {invoice.invoice_no}",
                      actor_id=actor_id)
    write_audit(db, actor_type="user", actor_id=actor_id, action="credit_note.create",
                entity_type="credit_note", entity_id=note.credit_note_no,
                after={"amount_paise": amount.paise, "invoice": invoice.invoice_no,
                       "reason": reason})
    db.commit()
    db.refresh(note)
    return note
