"""Notification admin endpoints (API_SPEC §7)."""
from __future__ import annotations

import uuid as uuid_mod

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.deps import Actor, Pagination, get_db, pagination, require
from app.core.exceptions import NotFound
from app.modules.customers.models import Customer
from app.modules.notifications.dispatch import attempt_send
from app.modules.notifications.models import Notification, NotificationPreference
from app.modules.notifications.templates import LANGUAGES, render

router = APIRouter(tags=["notifications"])


@router.get("/notifications")
def list_notifications(customer_uid: str | None = Query(None),
                       status: str | None = Query(None),
                       channel: str | None = Query(None),
                       page: Pagination = Depends(pagination),
                       db: Session = Depends(get_db),
                       actor: Actor = Depends(require("notification.manage", "credit.read"))):
    q = db.query(Notification)
    if customer_uid:
        customer = db.query(Customer).filter(Customer.uid == uuid_mod.UUID(customer_uid)).first()
        if customer is None:
            raise NotFound("Customer not found")
        q = q.filter(Notification.customer_id == customer.id)
    if status:
        q = q.filter(Notification.status == status)
    if channel:
        q = q.filter(Notification.channel == channel)
    if page.cursor is not None:
        q = q.filter(Notification.id < page.cursor)
    rows = q.order_by(Notification.id.desc()).limit(page.limit + 1).all()
    next_cursor = str(rows[page.limit - 1].id) if len(rows) > page.limit else None
    return {
        "items": [
            {"uid": str(n.uid), "template_key": n.template_key, "channel": n.channel,
             "language": n.language, "status": n.status, "recipient": n.recipient,
             "body": n.rendered_body, "attempts": n.attempts, "error": n.error,
             "sent_at": n.sent_at.isoformat() if n.sent_at else None,
             "created_at": n.created_at.isoformat()}
            for n in rows[: page.limit]
        ],
        "next_cursor": next_cursor,
    }


@router.post("/notifications/{notification_uid}/resend")
def resend(notification_uid: str, db: Session = Depends(get_db),
           actor: Actor = Depends(require("notification.manage"))):
    notification = db.query(Notification).filter(
        Notification.uid == uuid_mod.UUID(notification_uid)).first()
    if notification is None:
        raise NotFound("Notification not found")
    attempt_send(db, notification)
    db.commit()
    return {"uid": str(notification.uid), "status": notification.status}


class TestSendIn(BaseModel):
    template_key: str
    language: str = Field(default="en", pattern="^(en|hi|gu)$")
    variables: dict[str, str] = Field(default_factory=dict)


@router.post("/notifications/test-send")
def test_send(body: TestSendIn, db: Session = Depends(get_db),
              actor: Actor = Depends(require("notification.manage"))):
    """BR-NOT-08: preview every language before a template goes live."""
    previews = {}
    for lang in LANGUAGES:
        try:
            rendered, _ = render(db, body.template_key, "whatsapp", lang, body.variables)
            previews[lang] = rendered
        except Exception as exc:
            previews[lang] = f"ERROR: {exc}"
    return {"template_key": body.template_key, "previews": previews}


class PreferencesIn(BaseModel):
    whatsapp_enabled: bool | None = None
    sms_enabled: bool | None = None
    email_enabled: bool | None = None
    marketing_opt_out: bool | None = None


@router.get("/me/notification-preferences")
def get_preferences(db: Session = Depends(get_db), actor: Actor = Depends(require("customer"))):
    prefs = db.query(NotificationPreference).filter(
        NotificationPreference.customer_id == actor.customer_id).first()
    if prefs is None:
        return {"whatsapp_enabled": True, "sms_enabled": True, "email_enabled": True,
                "marketing_opt_out": False}
    return {"whatsapp_enabled": prefs.whatsapp_enabled, "sms_enabled": prefs.sms_enabled,
            "email_enabled": prefs.email_enabled, "marketing_opt_out": prefs.marketing_opt_out}


@router.patch("/me/notification-preferences")
def update_preferences(body: PreferencesIn, db: Session = Depends(get_db),
                       actor: Actor = Depends(require("customer"))):
    prefs = db.query(NotificationPreference).filter(
        NotificationPreference.customer_id == actor.customer_id).first()
    if prefs is None:
        prefs = NotificationPreference(customer_id=actor.customer_id)
        db.add(prefs)
    for field_name, value in body.model_dump().items():
        if value is not None:
            setattr(prefs, field_name, value)
    db.commit()
    return {"ok": True}
