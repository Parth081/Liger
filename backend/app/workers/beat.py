"""Celery Beat schedule (ARCHITECTURE §4). Times are IST (celery timezone).

Credit/score/digest jobs register here as their phases land (P3, P5, P7).
"""
from celery.schedules import crontab

from app.workers.celery_app import celery_app

celery_app.conf.beat_schedule = {
    "purge-idempotency-keys": {
        "task": "app.workers.tasks.maintenance.purge_idempotency_keys",
        "schedule": crontab(hour=4, minute=0),
    },
    # BR-CR-54: re-age, ladder, overrides, snapshots
    "nightly-credit-run": {
        "task": "app.workers.tasks.credit.nightly_credit_run",
        "schedule": crontab(hour=0, minute=30),
    },
    # BR-SCR-01
    "recompute-scores": {
        "task": "app.workers.tasks.credit.recompute_scores",
        "schedule": crontab(hour=2, minute=0),
    },
    # BR-LED-04
    "ledger-reconciliation": {
        "task": "app.workers.tasks.credit.ledger_reconciliation",
        "schedule": crontab(hour=3, minute=0),
    },
    # BR-CR-41…45 — sends run right after the ladder advances
    "ladder-notifications": {
        "task": "app.workers.tasks.notifications.send_ladder_notifications",
        "schedule": crontab(hour=1, minute=15),
    },
    # BR-CR-44 / BR-AN-06 — the day's call list
    "nightly-followups": {
        "task": "app.workers.tasks.fulfilment.nightly_followups",
        "schedule": crontab(hour=1, minute=30),
    },
    # BR-NOT-02 retry sweep + quiet-hour releases
    "retry-failed-notifications": {
        "task": "app.workers.tasks.notifications.retry_failed",
        "schedule": crontab(minute=5),
    },
    # BR-AN-07
    "owner-daily-digest": {
        "task": "app.workers.tasks.notifications.owner_daily_digest",
        "schedule": crontab(hour=8, minute=0),
    },
}
