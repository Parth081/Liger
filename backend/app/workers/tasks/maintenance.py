"""Maintenance jobs. Every job is idempotent and safe to re-run (ARCHITECTURE §4)."""
from app.db.session import get_session_factory
from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks.maintenance.purge_idempotency_keys")
def purge_idempotency_keys() -> int:
    from app.core.idempotency import purge_expired

    db = get_session_factory()()
    try:
        return purge_expired(db)
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.maintenance.ping")
def ping() -> str:
    """Smoke-test task (P0 DoD: 'a test job completes')."""
    return "pong"
