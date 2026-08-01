"""Fulfilment models (DATA_MODEL §8): production, dispatch, delivery, follow-ups."""
from __future__ import annotations

from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import ActorMixin, Base, PKMixin, TimestampMixin

_FK_INT = BigInteger().with_variant(Integer, "sqlite")

FOLLOW_UP_STATUS = sa.Enum("open", "in_progress", "done", "cancelled",
                           name="follow_up_status")
FOLLOW_UP_TYPE = sa.Enum(
    "payment_chase", "delivery_unpaid", "reorder_gap", "limit_review", "manual",
    name="follow_up_type",
)


class ProductionJob(Base, PKMixin, TimestampMixin, ActorMixin):
    __tablename__ = "production_jobs"

    order_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("orders.id"), index=True)
    stage: Mapped[str] = mapped_column(String(30), default="queued")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assigned_to: Mapped[int | None] = mapped_column(_FK_INT, ForeignKey("users.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class Dispatch(Base, PKMixin, TimestampMixin, ActorMixin):
    __tablename__ = "dispatches"

    order_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("orders.id"), index=True)
    transporter: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lr_no: Mapped[str | None] = mapped_column(String(60), nullable=True)
    vehicle_no: Mapped[str | None] = mapped_column(String(30), nullable=True)
    docket_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    dispatched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dispatched_by: Mapped[int | None] = mapped_column(_FK_INT, ForeignKey("users.id"),
                                                       nullable=True)


class Delivery(Base, PKMixin, TimestampMixin, ActorMixin):
    __tablename__ = "deliveries"

    order_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("orders.id"), index=True)
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    pod_image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_partial: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)


class FollowUpTask(Base, PKMixin, TimestampMixin, ActorMixin):
    """BR-CR-44, BR-AN-06 — the call list that actually gets money in."""

    __tablename__ = "follow_up_tasks"
    __table_args__ = (
        Index("ix_followups_assignee_status", "assignee_id", "status"),
        Index("ix_followups_due", "due_date", "status"),
        # one open auto-task per (customer, type, ref) — re-runs never pile up
        Index("uq_followup_dedupe", "dedupe_key", unique=True),
    )

    customer_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("customers.id"), index=True)
    type: Mapped[str] = mapped_column(FOLLOW_UP_TYPE)
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_date: Mapped[date] = mapped_column(Date)
    assignee_id: Mapped[int | None] = mapped_column(_FK_INT, ForeignKey("users.id"),
                                                     nullable=True)
    status: Mapped[str] = mapped_column(FOLLOW_UP_STATUS, default="open")
    outcome: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ref_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    ref_id: Mapped[int | None] = mapped_column(_FK_INT, nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(160))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
