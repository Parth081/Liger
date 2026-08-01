"""Dispatch — quiet hours, daily caps, de-duplication, channel fallback
(BR-NOT-02/05/06/09)."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import settings_registry
from app.db.base import utcnow
from app.modules.customers.models import Customer
from app.modules.notifications.models import Notification, NotificationPreference
from app.modules.notifications.providers import get_provider
from app.modules.notifications.templates import render

IST = ZoneInfo("Asia/Kolkata")
_MAX_ATTEMPTS = 5


def _in_quiet_hours(db: Session, now_utc: datetime) -> bool:
    start = time.fromisoformat(settings_registry.get_str(db, "quiet_hours_start"))
    end = time.fromisoformat(settings_registry.get_str(db, "quiet_hours_end"))
    now_ist = now_utc.astimezone(IST).time()
    if start <= end:
        return start <= now_ist < end
    return now_ist >= start or now_ist < end        # crosses midnight (21:00–08:00)


def _next_morning(db: Session, now_utc: datetime) -> datetime:
    end = time.fromisoformat(settings_registry.get_str(db, "quiet_hours_end"))
    now_ist = now_utc.astimezone(IST)
    candidate = now_ist.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if candidate <= now_ist:
        candidate += timedelta(days=1)
    return candidate.astimezone(ZoneInfo("UTC"))


def _daily_count(db: Session, customer_id: int, now_utc: datetime) -> int:
    day_start = now_utc.astimezone(IST).replace(hour=0, minute=0, second=0,
                                                microsecond=0).astimezone(ZoneInfo("UTC"))
    return int(
        db.query(func.count(Notification.id))
        .filter(Notification.customer_id == customer_id,
                Notification.created_at >= day_start,
                Notification.status.notin_(("failed", "dead")))
        .scalar()
    )


def notify_customer(
    db: Session,
    *,
    customer: Customer,
    template_key: str,
    variables: dict[str, str],
    dedupe_key: str,
    critical: bool = False,          # block notices ignore quiet hours (BR-NOT-05)
    is_marketing: bool = False,
) -> Notification | None:
    """Queue (and immediately attempt) one notification on the best channel.
    Returns None when suppressed (dedupe / cap / opt-out)."""
    # BR-NOT-06: dedupe — the same reminder never goes twice
    if db.query(Notification).filter(Notification.dedupe_key == dedupe_key).first():
        return None

    prefs = (
        db.query(NotificationPreference)
        .filter(NotificationPreference.customer_id == customer.id)
        .first()
    )
    # BR-NOT-09: opt-out honoured for marketing, never for transactional
    if is_marketing and prefs is not None and prefs.marketing_opt_out:
        return None

    # BR-NOT-06: daily cap (critical messages exempt)
    now = utcnow()
    if not critical:
        cap = settings_registry.get_int(db, "max_msgs_per_customer_per_day")
        if _daily_count(db, customer.id, now) >= cap:
            return None

    # channel preference: whatsapp -> sms -> email (BR-NOT-01)
    channels: list[str] = []
    if prefs is None or prefs.whatsapp_enabled:
        channels.append("whatsapp")
    if prefs is None or prefs.sms_enabled:
        channels.append("sms")
    if not channels:
        channels = ["sms"]                           # transactional always deliverable

    body, template = render(db, template_key, channels[0], customer.language, variables)
    notification = Notification(
        customer_id=customer.id,
        recipient=customer.primary_phone,
        channel=channels[0],
        template_key=template_key,
        language=customer.language,
        rendered_body=body,
        payload=variables,
        dedupe_key=dedupe_key,
    )

    # BR-NOT-05: quiet hours defer non-critical sends to morning
    if not critical and _in_quiet_hours(db, now):
        notification.status = "deferred"
        notification.scheduled_for = _next_morning(db, now)
        db.add(notification)
        db.commit()
        return notification

    db.add(notification)
    db.flush()
    attempt_send(db, notification, fallback_channels=channels[1:])
    db.commit()
    return notification


def attempt_send(db: Session, notification: Notification,
                 fallback_channels: list[str] | None = None) -> None:
    """One attempt + fallback (BR-NOT-02). Celery retries call this again."""
    provider = get_provider(notification.channel)
    notification.attempts += 1
    result = provider.send(recipient=notification.recipient,
                           body=notification.rendered_body)
    if result.ok:
        notification.status = "sent"
        notification.sent_at = utcnow()
        notification.provider_msg_id = result.provider_msg_id
        notification.error = None
        return

    notification.error = result.error
    if fallback_channels:
        # WhatsApp failed -> try SMS with the same content re-rendered
        next_channel = fallback_channels[0]
        try:
            body, _ = render(db, notification.template_key, next_channel,
                             notification.language, notification.payload or {})
            notification.channel = next_channel
            notification.rendered_body = body
            attempt_send(db, notification, fallback_channels=fallback_channels[1:])
            return
        except Exception:
            pass
    if notification.attempts >= _MAX_ATTEMPTS:
        notification.status = "dead"                 # dead-letter (BR-NOT-02)
    else:
        notification.status = "failed"               # retry sweep picks it up


def notify_admins(db: Session, *, template_key: str, variables: dict[str, str],
                  dedupe_key: str) -> int:
    """BR-NOT-03: configured admin numbers get operational alerts."""
    from app.modules.identity.models import Role, User

    admins = (
        db.query(User)
        .join(Role, Role.id == User.role_id)
        .filter(Role.code.in_(("super_admin", "admin")), User.is_active.is_(True),
                User.phone.isnot(None))
        .all()
    )
    sent = 0
    for admin in admins:
        key = f"{dedupe_key}:admin:{admin.id}"
        if db.query(Notification).filter(Notification.dedupe_key == key).first():
            continue
        body, _ = render(db, template_key, "whatsapp", "en", variables)
        notification = Notification(user_id=admin.id, recipient=admin.phone or "",
                                    channel="whatsapp", template_key=template_key,
                                    language="en", rendered_body=body,
                                    payload=variables, dedupe_key=key)
        db.add(notification)
        db.flush()
        attempt_send(db, notification, fallback_channels=["sms"])
        sent += 1
    db.commit()
    return sent


def retry_failed(db: Session) -> int:
    """Hourly sweep + release of quiet-hour deferrals (BR-NOT-02/05)."""
    now = utcnow()
    count = 0
    due = (
        db.query(Notification)
        .filter(
            (Notification.status == "failed")
            | ((Notification.status == "deferred") & (Notification.scheduled_for <= now))
        )
        .limit(500)
        .all()
    )
    for notification in due:
        attempt_send(db, notification)
        count += 1
    db.commit()
    return count
