"""Notification models (DATA_MODEL §9). BR-NOT-01…10."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin

_FK_INT = BigInteger().with_variant(Integer, "sqlite")
_JSON = JSON().with_variant(JSONB, "postgresql")

CHANNEL = sa.Enum("whatsapp", "sms", "email", "in_app", name="notification_channel")
NOTIFICATION_STATUS = sa.Enum(
    "queued", "deferred", "sent", "delivered", "read", "failed", "dead",
    name="notification_status",
)


class NotificationTemplate(Base, PKMixin, TimestampMixin):
    """BR-NOT-08: variable-driven, per (key, channel, language)."""

    __tablename__ = "notification_templates"
    __table_args__ = (UniqueConstraint("key", "channel", "language"),)

    key: Mapped[str] = mapped_column(String(60), index=True)
    channel: Mapped[str] = mapped_column(CHANNEL)
    language: Mapped[str] = mapped_column(String(5), default="en")     # DEC-10
    body: Mapped[str] = mapped_column(Text)
    provider_template_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    is_approved: Mapped[bool] = mapped_column(Boolean, default=False)  # provider approval
    is_transactional: Mapped[bool] = mapped_column(Boolean, default=True)  # BR-NOT-09


class Notification(Base, PKMixin, TimestampMixin):
    """One row per send attempt-chain. dedupe_key makes re-sends impossible
    (BR-NOT-06, BR-CR-49)."""

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("dedupe_key"),
        Index("ix_notifications_customer", "customer_id", "created_at"),
    )

    customer_id: Mapped[int | None] = mapped_column(_FK_INT, ForeignKey("customers.id"),
                                                    nullable=True)
    user_id: Mapped[int | None] = mapped_column(_FK_INT, ForeignKey("users.id"), nullable=True)
    recipient: Mapped[str] = mapped_column(String(120))         # phone or email
    channel: Mapped[str] = mapped_column(CHANNEL)
    template_key: Mapped[str] = mapped_column(String(60), index=True)
    language: Mapped[str] = mapped_column(String(5), default="en")
    rendered_body: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any] | None] = mapped_column(_JSON, nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(NOTIFICATION_STATUS, default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    provider_msg_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                           nullable=True)  # quiet hours
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NotificationPreference(Base, PKMixin, TimestampMixin):
    """BR-NOT-05/09 — per customer."""

    __tablename__ = "notification_preferences"

    customer_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("customers.id"), unique=True)
    whatsapp_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sms_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    marketing_opt_out: Mapped[bool] = mapped_column(Boolean, default=False)
