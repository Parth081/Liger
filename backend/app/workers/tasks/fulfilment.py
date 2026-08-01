"""Follow-up generation jobs (BR-CR-44, BR-AN-06). Idempotent on dedupe_key."""
from datetime import date

from app.db.session import get_session_factory
from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks.fulfilment.nightly_followups")
def nightly_followups() -> dict:
    """01:30 IST — after the ladder has advanced, build the day's call list."""
    from app.modules.fulfilment.service import nightly_followups as run

    db = get_session_factory()()
    try:
        return run(db, date.today())
    finally:
        db.close()
