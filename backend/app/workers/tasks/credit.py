"""Credit jobs (ARCHITECTURE §4). All idempotent — they key off state, never
off 'has this run today' (BR-CR-49, BR-CR-54)."""
from datetime import date

from app.db.session import get_session_factory
from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks.credit.nightly_credit_run")
def nightly_credit_run() -> dict:
    """00:30–01:00 IST: re-age, advance ladder, expire overrides, snapshots."""
    from app.modules.credit.service import nightly_credit_run as run

    db = get_session_factory()()
    try:
        return run(db, date.today())
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.credit.recompute_scores")
def recompute_scores() -> int:
    """02:00 IST (BR-SCR-01)."""
    from app.modules.credit.scoring import compute_score
    from app.modules.customers.models import Customer

    db = get_session_factory()()
    try:
        customers = db.query(Customer).filter(Customer.deleted_at.is_(None)).all()
        for customer in customers:
            compute_score(db, customer, date.today())
        return len(customers)
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.credit.ledger_reconciliation")
def ledger_reconciliation() -> dict:
    """03:00 IST — BR-LED-04: Σ ledger == derived outstanding, alert on drift."""
    from app.modules.credit.ledger import current_balance, derived_balance
    from app.modules.customers.models import Customer

    db = get_session_factory()()
    try:
        drift: list[str] = []
        for customer in db.query(Customer).filter(Customer.deleted_at.is_(None)).all():
            if current_balance(db, customer.id) != derived_balance(db, customer.id):
                drift.append(customer.code)
        if drift:
            # P5 turns this into an owner alert; failing loudly is the point
            raise RuntimeError(f"LEDGER DRIFT for customers: {', '.join(drift)}")
        return {"checked": db.query(Customer).count(), "drift": 0}
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.credit.on_payment_confirmed")
def on_payment_confirmed(customer_id: int) -> str:
    """BR-CR-47: real-time auto-unblock after a confirmed payment."""
    from app.modules.credit.service import reevaluate_block_state
    from app.modules.customers.models import Customer

    db = get_session_factory()()
    try:
        customer = db.get(Customer, customer_id)
        if customer is None:
            return "customer-missing"
        reevaluate_block_state(db, customer, date.today())
        return customer.status
    finally:
        db.close()
