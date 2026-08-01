"""Engine + session factory. Postgres in real environments; SQLite allowed in tests."""
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_config

_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        cfg = get_config()
        kwargs: dict = {"pool_pre_ping": True}
        if cfg.database_url.startswith("sqlite"):
            kwargs = {"connect_args": {"check_same_thread": False}}
        _engine = create_engine(cfg.database_url, **kwargs)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    # Load every module's models before the first session is built. Without
    # this, a process that imports only part of the app (a Celery task, a
    # one-off script) has an incomplete metadata graph and cross-module
    # foreign keys fail to resolve at query time.
    import app.db.all_models  # noqa: F401

    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency."""
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()


def reset_engine() -> None:
    """Test helper — forces re-read of DATABASE_URL."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
