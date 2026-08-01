"""Notification jobs (BR-NOT-02, BR-AN-07)."""
from datetime import date

from app.db.session import get_session_factory
from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks.notifications.send_ladder_notifications")
def send_ladder_notifications() -> int:
    """01:15 IST — right after the ladder advances (BR-CR-41…45)."""
    from app.modules.notifications.hooks import send_ladder_notifications as run

    db = get_session_factory()()
    try:
        return run(db, date.today())
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.notifications.retry_failed")
def retry_failed() -> int:
    """Hourly retry sweep + release of quiet-hour deferrals."""
    from app.modules.notifications.dispatch import retry_failed as run

    db = get_session_factory()()
    try:
        return run(db)
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.notifications.owner_daily_digest")
def owner_daily_digest() -> int:
    """08:00 IST (BR-AN-07): yesterday's orders, collections, new blocks."""
    from datetime import timedelta

    from sqlalchemy import func

    from app.core.money import Money
    from app.modules.credit.ledger import derived_balance
    from app.modules.credit.models import CreditEvent
    from app.modules.customers.models import Customer
    from app.modules.notifications.dispatch import notify_admins
    from app.modules.orders.models import Order
    from app.modules.payments.models import Payment

    db = get_session_factory()()
    try:
        yesterday = date.today() - timedelta(days=1)
        orders = (
            db.query(func.count(Order.id), func.coalesce(func.sum(Order.grand_total_paise), 0))
            .filter(Order.order_date == yesterday, Order.status.notin_(("CANCELLED", "DRAFT")))
            .one()
        )
        collections = int(
            db.query(func.coalesce(func.sum(Payment.amount_paise), 0))
            .filter(Payment.status == "confirmed",
                    func.date(Payment.confirmed_at) == yesterday)
            .scalar()
        )
        new_blocks = int(
            db.query(func.count(CreditEvent.id))
            .filter(CreditEvent.event_type == "blocked",
                    func.date(CreditEvent.created_at) == yesterday)
            .scalar()
        )
        outstanding = sum(
            derived_balance(db, c.id).paise
            for c in db.query(Customer).filter(Customer.deleted_at.is_(None)).all()
        )
        return notify_admins(
            db, template_key="admin.daily_digest",
            variables={
                "orders_count": str(orders[0]),
                "orders_value": Money(int(orders[1])).format_inr(),
                "collections": Money(collections).format_inr(),
                "new_blocks": str(new_blocks),
                "outstanding": Money(outstanding).format_inr(),
            },
            dedupe_key=f"digest:{yesterday}",
        )
    finally:
        db.close()
