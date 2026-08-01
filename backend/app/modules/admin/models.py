"""Operational models: settings (R8), audit log, idempotency keys (R6). DATA_MODEL §1/§10."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin

_FK_INT = BigInteger().with_variant(Integer, "sqlite")
_JSON = JSON().with_variant(JSONB, "postgresql")


class Setting(Base, PKMixin, TimestampMixin):
    """Every business rule value lives here (R8). Never hardcode 11, 15 days, 80%."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    value: Mapped[str] = mapped_column(String(255))
    value_type: Mapped[str] = mapped_column(String(10), default="str")  # str|int|decimal|bool
    group: Mapped[str] = mapped_column(String(40), default="general")
    description: Mapped[str] = mapped_column(String(255), default="")
    updated_by: Mapped[int | None] = mapped_column(_FK_INT, nullable=True)


class SettingHistory(Base, PKMixin):
    """Append-only trail of every settings change."""

    __tablename__ = "settings_history"

    key: Mapped[str] = mapped_column(String(80), index=True)
    old_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    new_value: Mapped[str] = mapped_column(String(255))
    changed_by: Mapped[int | None] = mapped_column(_FK_INT, nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditLog(Base, PKMixin):
    """Append-only (R2-adjacent). Every money/limit/price/permission change lands here."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id", "created_at"),)

    actor_type: Mapped[str] = mapped_column(String(20))  # user | customer_user | system
    actor_id: Mapped[int | None] = mapped_column(_FK_INT, nullable=True)
    action: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str] = mapped_column(String(60))
    before: Mapped[dict[str, Any] | None] = mapped_column(_JSON, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(_JSON, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class IdempotencyKey(Base, PKMixin, TimestampMixin):
    """R6: replay store for money-touching endpoints."""

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    endpoint: Mapped[str] = mapped_column(String(120))
    request_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict[str, Any] | None] = mapped_column(_JSON, nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NumberSeries(Base, PKMixin):
    """Gapless per-series counters (BR-ORD-06, BR-TAX-05).

    The next number is allocated with a row lock inside the caller's
    transaction — see core/numbering.py.
    """

    __tablename__ = "number_series"

    series: Mapped[str] = mapped_column(String(40), unique=True)  # e.g. order/2026-27, invoice/2026-27
    last_value: Mapped[int] = mapped_column(Integer, default=0)
