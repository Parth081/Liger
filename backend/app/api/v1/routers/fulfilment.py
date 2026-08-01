"""Fulfilment endpoints (API_SPEC §7) — production, dispatch, delivery, follow-ups."""
from __future__ import annotations

import uuid as uuid_mod
from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.deps import Actor, Pagination, get_db, pagination, require
from app.core.exceptions import Forbidden, NotFound
from app.modules.customers.models import Customer
from app.modules.fulfilment import service
from app.modules.fulfilment.models import FollowUpTask

router = APIRouter(tags=["fulfilment"])


class ProductionIn(BaseModel):
    assigned_to: int | None = None


@router.post("/orders/{order_uid}/production/start")
def start_production(order_uid: str, body: ProductionIn, db: Session = Depends(get_db),
                     actor: Actor = Depends(require("order.status.production"))):
    order = service.get_order_by_uid(db, order_uid)
    job = service.start_production(db, order, actor_id=actor.id, actor_role=actor.role,
                                   assigned_to=body.assigned_to)
    return {"order_no": order.order_no, "status": order.status, "job_uid": str(job.uid)}


@router.post("/orders/{order_uid}/production/ready")
def mark_ready(order_uid: str, db: Session = Depends(get_db),
               actor: Actor = Depends(require("order.status.production"))):
    order = service.get_order_by_uid(db, order_uid)
    service.mark_ready(db, order, actor_id=actor.id, actor_role=actor.role)
    return {"order_no": order.order_no, "status": order.status}


class DispatchIn(BaseModel):
    transporter: str | None = Field(default=None, max_length=120)
    lr_no: str | None = Field(default=None, max_length=60)
    vehicle_no: str | None = Field(default=None, max_length=30)
    docket_url: str | None = Field(default=None, max_length=500)


@router.post("/orders/{order_uid}/dispatch")
def dispatch_order(order_uid: str, body: DispatchIn, db: Session = Depends(get_db),
                   actor: Actor = Depends(require("order.status.dispatch"))):
    order = service.get_order_by_uid(db, order_uid)
    record = service.dispatch_order(db, order, transporter=body.transporter,
                                    lr_no=body.lr_no, vehicle_no=body.vehicle_no,
                                    docket_url=body.docket_url,
                                    actor_id=actor.id, actor_role=actor.role)
    return {"order_no": order.order_no, "status": order.status,
            "dispatch_uid": str(record.uid), "lr_no": record.lr_no}


class DeliverIn(BaseModel):
    received_by: str | None = Field(default=None, max_length=120)
    pod_image_url: str | None = Field(default=None, max_length=500)
    remarks: str | None = Field(default=None, max_length=500)
    is_partial: bool = False


@router.post("/orders/{order_uid}/deliver")
def deliver_order(order_uid: str, body: DeliverIn, db: Session = Depends(get_db),
                  actor: Actor = Depends(require("order.status.dispatch"))):
    order = service.get_order_by_uid(db, order_uid)
    record = service.deliver_order(db, order, received_by=body.received_by,
                                   pod_image_url=body.pod_image_url, remarks=body.remarks,
                                   is_partial=body.is_partial,
                                   actor_id=actor.id, actor_role=actor.role)
    return {"order_no": order.order_no, "status": order.status,
            "delivery_uid": str(record.uid)}


# ---------------- follow-up task board ----------------
def _task_payload(t: FollowUpTask, customer: Customer | None) -> dict:
    return {
        "uid": str(t.uid), "type": t.type, "title": t.title, "detail": t.detail,
        "due_date": str(t.due_date), "status": t.status, "outcome": t.outcome,
        "assignee_id": t.assignee_id,
        "customer": {"uid": str(customer.uid), "name": customer.business_name,
                     "phone": customer.primary_phone} if customer else None,
        "created_at": t.created_at.isoformat(),
    }


@router.get("/follow-ups")
def list_follow_ups(status: str | None = Query(None),
                    assignee_id: int | None = Query(None),
                    overdue: bool = Query(False),
                    page: Pagination = Depends(pagination),
                    db: Session = Depends(get_db),
                    actor: Actor = Depends(require("credit.read", "order.read"))):
    q = db.query(FollowUpTask)
    if actor.role == "sales_rep":
        q = q.filter(FollowUpTask.assignee_id == actor.id)      # BR-AC-04
    elif assignee_id is not None:
        q = q.filter(FollowUpTask.assignee_id == assignee_id)
    if status:
        q = q.filter(FollowUpTask.status == status)
    if overdue:
        q = q.filter(FollowUpTask.status.in_(("open", "in_progress")),
                     FollowUpTask.due_date < date.today())
    if page.cursor is not None:
        q = q.filter(FollowUpTask.id < page.cursor)
    rows = q.order_by(FollowUpTask.id.desc()).limit(page.limit + 1).all()
    next_cursor = str(rows[page.limit - 1].id) if len(rows) > page.limit else None
    items = []
    for t in rows[: page.limit]:
        items.append(_task_payload(t, db.get(Customer, t.customer_id)))
    return {"items": items, "next_cursor": next_cursor}


class TaskCreateIn(BaseModel):
    customer_uid: str
    title: str = Field(min_length=3, max_length=200)
    detail: str | None = Field(default=None, max_length=1000)
    due_date: date
    assignee_id: int | None = None


@router.post("/follow-ups", status_code=201)
def create_follow_up(body: TaskCreateIn, db: Session = Depends(get_db),
                     actor: Actor = Depends(require("credit.read", "order.read"))):
    customer = db.query(Customer).filter(
        Customer.uid == uuid_mod.UUID(body.customer_uid)).first()
    if customer is None:
        raise NotFound("Customer not found")
    task = service.create_task(
        db, customer=customer, task_type="manual", title=body.title, detail=body.detail,
        due_date=body.due_date, assignee_id=body.assignee_id or actor.id,
        dedupe_key=f"manual:{customer.id}:{body.title}:{body.due_date}",
        actor_id=actor.id,
    )
    if task is None:
        raise NotFound("An identical task already exists")
    return _task_payload(task, customer)


class TaskCloseIn(BaseModel):
    outcome: str = Field(min_length=3, max_length=255)


@router.post("/follow-ups/{task_uid}/close")
def close_follow_up(task_uid: str, body: TaskCloseIn, db: Session = Depends(get_db),
                    actor: Actor = Depends(require("credit.read", "order.read"))):
    task = db.query(FollowUpTask).filter(FollowUpTask.uid == uuid_mod.UUID(task_uid)).first()
    if task is None:
        raise NotFound("Follow-up not found")
    if actor.role == "sales_rep" and task.assignee_id != actor.id:
        raise Forbidden("Not your follow-up")
    service.close_task(db, task, outcome=body.outcome, actor_id=actor.id)
    return _task_payload(task, db.get(Customer, task.customer_id))
