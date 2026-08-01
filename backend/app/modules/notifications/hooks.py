"""Event → notification wiring (BR-NOT-03) and the ladder sender (P5-T5-02).

Handlers open their own session — a notification failure must never break the
request that emitted the event (R10).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.core import events, settings_registry
from app.core.money import Money
from app.db.session import get_session_factory
from app.modules.credit.models import EscalationState, Invoice
from app.modules.customers.models import Customer
from app.modules.notifications.dispatch import notify_admins, notify_customer

logger = logging.getLogger("liger.notifications")

_STEP_TEMPLATES = {
    "pre_due": "credit.pre_due",       # BR-CR-41
    "due_today": "credit.due_today",   # BR-CR-42
    "warn1": "credit.warn1",           # BR-CR-43
    "warn2": "credit.warn2_final",     # BR-CR-44
    "block": "credit.blocked",         # BR-CR-45
}


def send_ladder_notifications(db: Session, on: date) -> int:
    """Send every fired-but-unnotified ladder step. Dedupe key = invoice+step,
    so a re-run sends nothing twice (BR-CR-49). Shadow mode records steps but
    this sender skips them until enforcement — dealers are not nagged by a
    system that is still being tuned (BR-CR-40)."""
    if settings_registry.get_str(db, "credit_enforcement_mode") != "enforce":
        return 0
    pending = (
        db.query(EscalationState)
        .filter(EscalationState.notified.is_(False))
        .all()
    )
    block_day = settings_registry.get_int(db, "ladder_block")
    warn2_day = settings_registry.get_int(db, "ladder_warn2")
    sent = 0
    for row in pending:
        invoice = db.get(Invoice, row.invoice_id)
        customer = db.get(Customer, row.customer_id)
        if invoice is None or customer is None:
            row.notified = True
            continue
        days_overdue = max(0, (on - invoice.due_date).days)
        variables = {
            "invoice_no": invoice.invoice_no,
            "amount": Money(invoice.outstanding_paise).format_inr(),
            "outstanding": Money(invoice.outstanding_paise).format_inr(),
            "due_date": str(invoice.due_date),
            "days_overdue": str(days_overdue),
            "days_to_block": str(max(0, block_day - warn2_day)),
            "pay_link": "https://liger.in/pay",     # payment-link service wires per-invoice links
        }
        notify_customer(
            db, customer=customer,
            template_key=_STEP_TEMPLATES[row.step],
            variables=variables,
            dedupe_key=f"ladder:{row.invoice_id}:{row.step}",
            critical=(row.step == "block"),          # block notices ignore quiet hours
        )
        if row.step in ("warn2", "block"):
            # BR-CR-44/45: admin copied on final warning and block
            notify_admins(db, template_key=_STEP_TEMPLATES[row.step],
                          variables=variables,
                          dedupe_key=f"ladder:{row.invoice_id}:{row.step}")
        row.notified = True
        sent += 1
    db.commit()
    return sent


# ---------------- event handlers (registered at startup) ----------------
def _with_session(handler):
    def wrapped(payload: dict[str, Any]) -> None:
        db = get_session_factory()()
        try:
            handler(db, payload)
        except Exception:
            logger.exception("notification handler failed")
        finally:
            db.close()

    return wrapped


@_with_session
def _on_order_placed(db: Session, payload: dict[str, Any]) -> None:
    from app.modules.orders.models import Order

    order = db.get(Order, payload["order_id"])
    customer = db.get(Customer, payload["customer_id"]) if order else None
    if order is None or customer is None:
        return
    notify_customer(
        db, customer=customer, template_key="order.placed",
        variables={
            "order_no": order.order_no,
            "item_count": str(len(order.items)),
            "total": Money(order.grand_total_paise).format_inr(),
            "expected_delivery": str(order.expected_delivery_date or "to be confirmed"),
        },
        dedupe_key=f"order.placed:{order.id}",
    )


@_with_session
def _on_order_status(db: Session, payload: dict[str, Any]) -> None:
    from app.modules.orders.models import Order

    order = db.get(Order, payload["order_id"])
    if order is None:
        return
    customer = db.get(Customer, order.customer_id)
    if customer is None:
        return
    notify_customer(
        db, customer=customer, template_key="order.status",
        variables={"order_no": order.order_no, "status": payload["to"], "extra": ""},
        dedupe_key=f"order.status:{order.id}:{payload['to']}",
    )


@_with_session
def _on_payment_confirmed(db: Session, payload: dict[str, Any]) -> None:
    from app.modules.credit.ledger import current_balance
    from app.modules.payments.models import Payment

    payment = db.get(Payment, payload["payment_id"])
    customer = db.get(Customer, payload["customer_id"])
    if payment is None or customer is None:
        return
    notify_customer(
        db, customer=customer, template_key="payment.received",
        variables={
            "amount": Money(payment.amount_paise).format_inr(),
            "method": payment.method,
            "outstanding": current_balance(db, customer.id).format_inr(),
        },
        dedupe_key=f"payment.received:{payment.id}",
    )


@_with_session
def _on_cash_pending(db: Session, payload: dict[str, Any]) -> None:
    from app.modules.payments.models import Payment

    payment = db.get(Payment, payload["payment_id"])
    customer = db.get(Customer, payload["customer_id"])
    if payment is None or customer is None:
        return
    staff = "staff"
    if payment.recorded_by:
        from app.modules.identity.models import User

        user = db.get(User, payment.recorded_by)
        staff = user.name if user else "staff"
    notify_admins(
        db, template_key="payment.cash_pending",
        variables={"staff": staff, "amount": Money(payment.amount_paise).format_inr(),
                   "customer": customer.business_name},
        dedupe_key=f"cash.pending:{payment.id}",
    )


_registered = False


def register_handlers() -> None:
    """Called from create_app(); idempotent — tests create many apps."""
    global _registered
    if _registered:
        return
    _registered = True
    events.on("order.placed", _on_order_placed)
    events.on("order.status_changed", _on_order_status)
    events.on("payment.confirmed", _on_payment_confirmed)
    events.on("cash.pending_confirmation", _on_cash_pending)
