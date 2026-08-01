"""Celery application — queues per ARCHITECTURE §4 (R10)."""
from celery import Celery

from app.core.config import get_config

cfg = get_config()

celery_app = Celery(
    "liger",
    broker=cfg.redis_url,
    backend=cfg.redis_url,
    include=[
        "app.workers.tasks.maintenance",
        "app.workers.tasks.credit",
        "app.workers.tasks.notifications",
        "app.workers.tasks.fulfilment",
    ],
)

celery_app.conf.update(
    task_default_queue="critical",
    task_routes={
        "app.workers.tasks.notifications.*": {"queue": "notifications"},
        "app.workers.tasks.reports.*": {"queue": "reports"},
        "app.workers.tasks.maintenance.*": {"queue": "reports"},
        "app.workers.tasks.credit.*": {"queue": "critical"},
        "app.workers.tasks.webhooks.*": {"queue": "critical"},
    },
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    timezone="Asia/Kolkata",
    enable_utc=True,
)
