"""BR-CR-20 / R7 — the concurrency gate, on real Postgres.

Two simultaneous orders, each individually within the limit but jointly over
it: exactly one must be accepted. SQLite cannot express `SELECT … FOR UPDATE`,
so this test is skipped unless a Postgres DATABASE_URL is supplied:

    $env:TEST_POSTGRES_URL = "postgresql+psycopg2://liger_user:liger_8010@localhost:5433/liger_test"
    pytest tests/credit/test_concurrency_postgres.py

This is a P8 exit-gate test (P8-T6-02) and runs in CI against the Postgres
service container.
"""
from __future__ import annotations

import os
import threading
from datetime import date

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core import settings_registry
from app.core.money import Money
from app.db.base import Base
from app.modules.credit import gate
from app.modules.customers.models import Customer

PG_URL = os.environ.get("TEST_POSTGRES_URL") or os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not PG_URL.startswith("postgresql"),
    reason="needs a Postgres DATABASE_URL/TEST_POSTGRES_URL (row locks)",
)


@pytest.fixture(scope="module")
def pg_engine():
    import app.db.all_models  # noqa: F401 — full metadata

    engine = create_engine(PG_URL)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def pg_sessions(pg_engine):
    factory = sessionmaker(bind=pg_engine, autoflush=False, expire_on_commit=False)
    setup = factory()
    for table in reversed(Base.metadata.sorted_tables):
        setup.execute(text(f'TRUNCATE TABLE "{table.name}" CASCADE'))
    setup.commit()
    from app.db.seed import seed_all

    seed_all(setup)
    settings_registry.set_value(setup, "credit_enforcement_mode", "enforce", actor_id=None)
    yield factory, setup
    setup.close()


def test_BR_CR_20_two_concurrent_orders_cannot_both_pass(pg_sessions):
    """Each order is ₹6,000 against a ₹10,000 limit. Together they exceed it.

    Without the row lock both threads read the same exposure and both pass —
    the dealer walks away with ₹12,000 of credit on a ₹10,000 limit.
    """
    factory, setup = pg_sessions
    customer = Customer(code="CONC01", business_name="Concurrency Traders",
                        primary_phone="+919000099001", state="GJ",
                        credit_limit_paise=1_000_000)   # ₹10,000
    setup.add(customer)
    setup.commit()
    customer_id = customer.id

    order_total = Money(600_000)                        # ₹6,000 each
    results: list[str] = []
    barrier = threading.Barrier(2)
    lock = threading.Lock()

    def place_order() -> None:
        session = factory()
        try:
            barrier.wait(timeout=10)                    # start together
            locked = gate.lock_customer(session, customer_id)   # R7: FOR UPDATE
            assert locked is not None
            decision = gate.evaluate(session, locked, order_total, on=date.today())
            if decision.decision in ("ALLOW", "WARN"):
                # simulate persisting the order's exposure inside the same txn
                locked.credit_limit_paise = locked.credit_limit_paise  # touch the row
                session.execute(
                    text("INSERT INTO ledger_entries "
                         "(uid, customer_id, entry_type, debit_paise, credit_paise, "
                         " balance_after_paise, posted_at) "
                         "VALUES (gen_random_uuid(), :cid, 'invoice', :amt, 0, :amt, now())"),
                    {"cid": customer_id, "amt": order_total.paise},
                )
            session.commit()
            with lock:
                results.append(decision.decision)
        finally:
            session.close()

    threads = [threading.Thread(target=place_order) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(results) == 2, f"both threads must finish, got {results}"
    accepted = [r for r in results if r in ("ALLOW", "WARN")]
    assert len(accepted) == 1, (
        f"exactly one order may pass a shared limit, got {results}"
    )


def test_BR_ORD_06_invoice_numbering_gapless_under_concurrency(pg_sessions):
    """P4-T6-06: 20 concurrent allocations produce 20 consecutive numbers."""
    from app.core.numbering import next_invoice_no

    factory, _ = pg_sessions
    today = date(2026, 8, 1)
    numbers: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(20)

    def allocate() -> None:
        session = factory()
        try:
            barrier.wait(timeout=15)
            number = next_invoice_no(session, today)
            session.commit()
            with lock:
                numbers.append(number)
        finally:
            session.close()

    threads = [threading.Thread(target=allocate) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(numbers) == 20
    sequence = sorted(int(n.rsplit("/", 1)[1]) for n in numbers)
    assert sequence == list(range(1, 21)), f"gaps or duplicates: {sequence}"
