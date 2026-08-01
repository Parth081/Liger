"""Fulfilment service — production, dispatch, delivery, follow-up tasks.

BR-CR-46 is the rule that matters most here: blocking stops NEW orders; work
already in production runs to completion and keeps being chased.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.exceptions import NotFound, ValidationFailed
from app.core.money import Money
from app.db.base import utcnow
from app.modules.credit.models import Invoice
from app.modules.customers.models import Customer
from app.modules.fulfilment.models import (
    Delivery,
    Dispatch,
    FollowUpTask,
    ProductionJob,
)
from app.modules.orders import service as order_service
from app.modules.orders.models import Order


# ---------------- production ----------------
def start_production(db: Session, order: Order, *, actor_id: int, actor_role: str,
                     assigned_to: int | None = None) -> ProductionJob:
    order_service.change_status(db, order, "IN_PRODUCTION", actor_type="user",
                                actor_id=actor_id, actor_role=actor_role)
    job = ProductionJob(order_id=order.id, stage="cutting", started_at=utcnow(),
                        assigned_to=assigned_to, created_by=actor_id)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def mark_ready(db: Session, order: Order, *, actor_id: int, actor_role: str) -> Order:
    job = (
        db.query(ProductionJob)
        .filter(ProductionJob.order_id == order.id)
        .order_by(ProductionJob.id.desc())
        .first()
    )
    if job is not None:
        job.stage = "ready"
        job.completed_at = utcnow()
    return order_service.change_status(db, order, "READY", actor_type="user",
                                       actor_id=actor_id, actor_role=actor_role)


# ---------------- dispatch ----------------
def dispatch_order(db: Session, order: Order, *, transporter: str | None, lr_no: str | None,
                   vehicle_no: str | None, docket_url: str | None,
                   actor_id: int, actor_role: str) -> Dispatch:
    record = Dispatch(order_id=order.id, transporter=transporter, lr_no=lr_no,
                      vehicle_no=vehicle_no, docket_url=docket_url,
                      dispatched_at=utcnow(), dispatched_by=actor_id, created_by=actor_id)
    db.add(record)
    order_service.change_status(db, order, "DISPATCHED", actor_type="user",
                                actor_id=actor_id, actor_role=actor_role,
                                reason=f"LR {lr_no}" if lr_no else None)
    db.refresh(record)
    return record


# ---------------- delivery ----------------
def deliver_order(db: Session, order: Order, *, received_by: str | None,
                  pod_image_url: str | None, remarks: str | None, is_partial: bool,
                  actor_id: int, actor_role: str, on: date | None = None) -> Delivery:
    """On full delivery the order moves to DELIVERED and, if unpaid, a payment
    follow-up chain starts (BR-CR-46, P6-T2-07)."""
    on = on or date.today()
    record = Delivery(order_id=order.id, delivered_at=utcnow(), received_by=received_by,
                      pod_image_url=pod_image_url, remarks=remarks, is_partial=is_partial,
                      created_by=actor_id)
    db.add(record)
    target = "PARTIALLY_DELIVERED" if is_partial else "DELIVERED"
    order_service.change_status(db, order, target, actor_type="user", actor_id=actor_id,
                                actor_role=actor_role, reason=received_by)

    if not is_partial:
        _on_delivered_unpaid(db, order, on)
    db.refresh(record)
    return record


def _on_delivered_unpaid(db: Session, order: Order, on: date) -> None:
    """P6-T2-07: delivered but not yet settled -> follow-up task."""
    invoice = (
        db.query(Invoice)
        .filter(Invoice.order_id == order.id, Invoice.status == "open")
        .first()
    )
    outstanding = invoice.outstanding_paise if invoice else order.grand_total_paise
    if outstanding <= 0:
        return
    customer = db.get(Customer, order.customer_id)
    if customer is None:
        return
    create_task(
        db,
        customer=customer,
        task_type="delivery_unpaid",
        title=f"Collect {Money(outstanding).format_inr()} for {order.order_no}",
        detail=f"Order {order.order_no} delivered on {on}. Payment still outstanding.",
        due_date=on + timedelta(days=3),
        ref_type="order", ref_id=order.id,
        dedupe_key=f"delivery_unpaid:{order.id}",
    )


# ---------------- follow-up tasks ----------------
def create_task(db: Session, *, customer: Customer, task_type: str, title: str,
                detail: str | None, due_date: date, dedupe_key: str,
                ref_type: str | None = None, ref_id: int | None = None,
                assignee_id: int | None = None, actor_id: int | None = None) -> FollowUpTask | None:
    """Idempotent on dedupe_key — nightly jobs never pile duplicates."""
    existing = (
        db.query(FollowUpTask)
        .filter(FollowUpTask.dedupe_key == dedupe_key)
        .first()
    )
    if existing is not None:
        return None
    task = FollowUpTask(
        customer_id=customer.id, type=task_type, title=title, detail=detail,
        due_date=due_date, dedupe_key=dedupe_key, ref_type=ref_type, ref_id=ref_id,
        # default owner is the customer's own sales rep (P6-T2-06)
        assignee_id=assignee_id or customer.sales_rep_id,
        created_by=actor_id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def close_task(db: Session, task: FollowUpTask, *, outcome: str, actor_id: int) -> FollowUpTask:
    if not outcome.strip():
        raise ValidationFailed("Log what happened before closing a follow-up")
    task.status = "done"
    task.outcome = outcome.strip()
    task.closed_at = utcnow()
    task.updated_by = actor_id
    write_audit(db, actor_type="user", actor_id=actor_id, action="followup.close",
                entity_type="follow_up", entity_id=str(task.uid),
                after={"outcome": outcome})
    db.commit()
    return task


def reassign_task(db: Session, task: FollowUpTask, *, assignee_id: int,
                  actor_id: int) -> FollowUpTask:
    task.assignee_id = assignee_id
    task.updated_by = actor_id
    db.commit()
    return task


# ---------------- nightly generators (BR-AN-06) ----------------
def generate_warn2_tasks(db: Session, on: date) -> int:
    """BR-CR-44: the final warning creates a human call task."""
    from app.modules.credit.models import EscalationState

    rows = (
        db.query(EscalationState)
        .filter(EscalationState.step == "warn2", EscalationState.fired_on >= on - timedelta(days=1))
        .all()
    )
    created = 0
    for row in rows:
        customer = db.get(Customer, row.customer_id)
        invoice = db.get(Invoice, row.invoice_id)
        if customer is None or invoice is None or invoice.status != "open":
            continue
        task = create_task(
            db, customer=customer, task_type="payment_chase",
            title=f"Call {customer.business_name} — final warning on {invoice.invoice_no}",
            detail=(f"{Money(invoice.outstanding_paise).format_inr()} overdue since "
                    f"{invoice.due_date}. Account blocks soon."),
            due_date=on, ref_type="invoice", ref_id=invoice.id,
            dedupe_key=f"warn2:{invoice.id}",
        )
        if task is not None:
            created += 1
    return created


def generate_reorder_gap_tasks(db: Session, on: date) -> int:
    """BR-AN-06: 'ordered every month for 2 years — nothing in 47 days. Call them.'

    Compares each customer's own rhythm against their silence, so a quarterly
    buyer is not chased like a monthly one.
    """
    created = 0
    year_ago = on - timedelta(days=365)
    rows = (
        db.query(
            Order.customer_id,
            func.count(Order.id),
            func.max(Order.order_date),
            func.min(Order.order_date),
        )
        .filter(Order.order_date >= year_ago,
                Order.status.notin_(("CANCELLED", "DRAFT")))
        .group_by(Order.customer_id)
        .all()
    )
    for customer_id, order_count, last_order, first_order in rows:
        if order_count < 4:                     # too little history to judge a gap
            continue
        span_days = max((last_order - first_order).days, 1)
        avg_gap = span_days / max(order_count - 1, 1)
        silence = (on - last_order).days
        if silence < max(avg_gap * 2, 30):      # 2x their own rhythm, min 30 days
            continue
        customer = db.get(Customer, customer_id)
        if customer is None or customer.deleted_at is not None:
            continue
        task = create_task(
            db, customer=customer, task_type="reorder_gap",
            title=f"Call {customer.business_name} — no order in {silence} days",
            detail=(f"Ordered {order_count} times in the last year "
                    f"(about every {avg_gap:.0f} days). Last order {last_order}."),
            due_date=on, ref_type="customer", ref_id=customer.id,
            dedupe_key=f"reorder_gap:{customer.id}:{last_order}",
        )
        if task is not None:
            created += 1
    return created


def nightly_followups(db: Session, on: date) -> dict[str, int]:
    return {
        "warn2": generate_warn2_tasks(db, on),
        "reorder_gap": generate_reorder_gap_tasks(db, on),
    }


def get_order_by_uid(db: Session, order_uid: str) -> Order:
    import uuid as uuid_mod

    order = db.query(Order).filter(Order.uid == uuid_mod.UUID(order_uid)).first()
    if order is None:
        raise NotFound("Order not found")
    return order
