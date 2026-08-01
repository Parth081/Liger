"""Test fixtures. Unit tests run on SQLite; Postgres-only behaviour (row locks
under concurrency, partial indexes) is exercised in the P8 suite against a
real Postgres.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_liger.db")
os.environ.setdefault("ENV", "local")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import app.db.all_models  # noqa: F401 — registers every table on Base.metadata
from app.core import security
from app.core.settings_registry import clear_cache
from app.db import session as db_session
from app.db.base import Base
from app.db.seed import seed_all, seed_super_admin
from app.main import create_app
from app.modules.identity.models import CustomerUser, Role, User


@pytest.fixture(scope="session")
def engine():
    db_session.reset_engine()
    eng = db_session.get_engine()
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)  # tests only — production uses Alembic (R5)
    # R2 append-only triggers (created by migration 0004 in real envs)
    from sqlalchemy import text

    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TRIGGER IF NOT EXISTS trg_ledger_no_update BEFORE UPDATE ON ledger_entries "
            "BEGIN SELECT RAISE(ABORT, 'ledger_entries is append-only (R2)'); END;"
        ))
        conn.execute(text(
            "CREATE TRIGGER IF NOT EXISTS trg_ledger_no_delete BEFORE DELETE ON ledger_entries "
            "BEGIN SELECT RAISE(ABORT, 'ledger_entries is append-only (R2)'); END;"
        ))
    yield eng
    Base.metadata.drop_all(eng)
    db_session.reset_engine()
    if os.path.exists("test_liger.db"):
        try:
            os.remove("test_liger.db")
        except PermissionError:
            pass


@pytest.fixture()
def db(engine) -> Session:
    """Function-scoped session with clean tables per test."""
    from sqlalchemy import text

    clear_cache()
    factory = db_session.get_session_factory()
    s = factory()
    # wipe all rows between tests, keep schema. The ledger triggers (R2) block
    # DELETE by design — lift them for the wipe, restore after.
    s.execute(text("DROP TRIGGER IF EXISTS trg_ledger_no_update"))
    s.execute(text("DROP TRIGGER IF EXISTS trg_ledger_no_delete"))
    for table in reversed(Base.metadata.sorted_tables):
        s.execute(table.delete())
    s.execute(text(
        "CREATE TRIGGER trg_ledger_no_update BEFORE UPDATE ON ledger_entries "
        "BEGIN SELECT RAISE(ABORT, 'ledger_entries is append-only (R2)'); END;"
    ))
    s.execute(text(
        "CREATE TRIGGER trg_ledger_no_delete BEFORE DELETE ON ledger_entries "
        "BEGIN SELECT RAISE(ABORT, 'ledger_entries is append-only (R2)'); END;"
    ))
    s.commit()
    seed_all(s)
    yield s
    s.rollback()
    s.close()


@pytest.fixture()
def client(db) -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


@pytest.fixture()
def super_admin(db) -> User:
    return seed_super_admin(db, "owner@ligertest.com", "owner-pass-123", "Owner")


@pytest.fixture()
def staff_factory(db):
    def make(role_code: str, email: str | None = None) -> User:
        address = email or f"{role_code}@ligertest.com"
        existing = db.query(User).filter(User.email == address).first()
        if existing is not None:
            return existing
        role = db.query(Role).filter(Role.code == role_code).one()
        user = User(
            name=f"{role_code} user",
            email=address,
            password_hash=security.hash_password("pass-12345"),
            role_id=role.id,
        )
        db.add(user)
        db.commit()
        return user

    return make


@pytest.fixture()
def dealer(db) -> CustomerUser:
    cu = CustomerUser(name="Dealer One", phone="+919876500001", customer_id=1)
    db.add(cu)
    db.commit()
    return cu


def auth_header(actor_type: str, actor_id: int, role: str, customer_id: int | None = None) -> dict:
    token = security.create_access_token(actor_type, actor_id, role, customer_id=customer_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def as_staff(staff_factory):
    def make(role_code: str) -> dict:
        user = staff_factory(role_code)
        return auth_header("user", user.id, role_code)

    return make


@pytest.fixture()
def as_dealer(dealer):
    return auth_header("customer_user", dealer.id, "customer", customer_id=dealer.customer_id)


# ---------------- P1 fixtures ----------------
@pytest.fixture()
def design_factory(db):
    """Creates designs in seeded categories with a base rate."""
    from decimal import Decimal

    from app.modules.catalog.models import Category, Design

    def make(design_no: str, *, rate_rupees: str = "125", gst_pct: str = "12",
             category_code: str = "ZEBRA", status: str = "active",
             hsn: str | None = "5407") -> Design:
        category = db.query(Category).filter(Category.code == category_code).one()
        d = Design(
            design_no=design_no,
            name=f"Design {design_no}",
            category_id=category.id,
            gst_pct=Decimal(gst_pct),
            base_rate_paise=int(Decimal(rate_rupees) * 100),
            hsn_code=hsn,
            status=status,
        )
        db.add(d)
        db.commit()
        db.refresh(d)
        return d

    return make


@pytest.fixture()
def customer_factory(db):
    from app.modules.customers.models import Customer

    _counter = {"n": 0}

    def make(*, state: str = "GJ", credit_limit_rupees: int = 500000,
             credit_days: int = 30, phone: str | None = None) -> Customer:
        _counter["n"] += 1
        n = _counter["n"]
        c = Customer(
            code=f"CUST{n:04d}",
            business_name=f"Test Traders {n}",
            primary_phone=phone or f"+91987650{n:04d}",
            state=state,
            credit_limit_paise=credit_limit_rupees * 100,
            credit_days=credit_days,
        )
        db.add(c)
        db.commit()
        db.refresh(c)
        return c

    return make
