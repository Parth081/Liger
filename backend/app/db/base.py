"""Declarative base and shared mixins (DATA_MODEL.md global conventions)."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, Integer, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(dt: datetime | None) -> datetime | None:
    """Coerce a possibly-naive DB datetime to aware UTC.

    Postgres timestamptz round-trips aware; SQLite (tests) drops tzinfo.
    All naive values in this system are UTC by construction.
    """
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    pass


class PKMixin:
    """Internal bigint PK + public UUID (the only id exposed in APIs)."""

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"),
                                    primary_key=True, autoincrement=True)
    uid: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, default=uuid.uuid4, index=True)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ActorMixin:
    """Who created/last touched the row. Nullable for system actions."""

    created_by: Mapped[int | None] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), nullable=True)
    updated_by: Mapped[int | None] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), nullable=True)


class VersionMixin:
    """Optimistic locking on mutable entities."""

    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class SoftDeleteMixin:
    """Masters only — never ledger/orders/invoices/audit (DATA_MODEL conventions)."""

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
