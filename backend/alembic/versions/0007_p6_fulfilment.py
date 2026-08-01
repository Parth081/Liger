"""P6: production jobs, dispatches, deliveries, follow-up tasks.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-01
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")

follow_up_status = sa.Enum("open", "in_progress", "done", "cancelled",
                           name="follow_up_status")
follow_up_type = sa.Enum("payment_chase", "delivery_unpaid", "reorder_gap",
                         "limit_review", "manual", name="follow_up_type")


def _std() -> list[sa.Column]:
    return [
        sa.Column("id", _BIGID, primary_key=True, autoincrement=True),
        sa.Column("uid", sa.Uuid(), nullable=False, unique=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", _BIGID, nullable=True),
        sa.Column("updated_by", _BIGID, nullable=True),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        follow_up_status.create(bind, checkfirst=True)
        follow_up_type.create(bind, checkfirst=True)

    op.create_table(
        "production_jobs",
        *_std(),
        sa.Column("order_id", _BIGID, sa.ForeignKey("orders.id"), nullable=False, index=True),
        sa.Column("stage", sa.String(30), nullable=False, server_default="queued"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assigned_to", _BIGID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )

    op.create_table(
        "dispatches",
        *_std(),
        sa.Column("order_id", _BIGID, sa.ForeignKey("orders.id"), nullable=False, index=True),
        sa.Column("transporter", sa.String(120), nullable=True),
        sa.Column("lr_no", sa.String(60), nullable=True),
        sa.Column("vehicle_no", sa.String(30), nullable=True),
        sa.Column("docket_url", sa.String(500), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_by", _BIGID, sa.ForeignKey("users.id"), nullable=True),
    )

    op.create_table(
        "deliveries",
        *_std(),
        sa.Column("order_id", _BIGID, sa.ForeignKey("orders.id"), nullable=False, index=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_by", sa.String(120), nullable=True),
        sa.Column("pod_image_url", sa.String(500), nullable=True),
        sa.Column("is_partial", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("remarks", sa.Text(), nullable=True),
    )

    op.create_table(
        "follow_up_tasks",
        *_std(),
        sa.Column("customer_id", _BIGID, sa.ForeignKey("customers.id"), nullable=False,
                  index=True),
        sa.Column("type", follow_up_type, nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("assignee_id", _BIGID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("status", follow_up_status, nullable=False, server_default="open"),
        sa.Column("outcome", sa.String(255), nullable=True),
        sa.Column("ref_type", sa.String(30), nullable=True),
        sa.Column("ref_id", _BIGID, nullable=True),
        sa.Column("dedupe_key", sa.String(160), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_followups_assignee_status", "follow_up_tasks",
                    ["assignee_id", "status"])
    op.create_index("ix_followups_due", "follow_up_tasks", ["due_date", "status"])
    op.create_index("uq_followup_dedupe", "follow_up_tasks", ["dedupe_key"], unique=True)


def downgrade() -> None:
    for table in ("follow_up_tasks", "deliveries", "dispatches", "production_jobs"):
        op.drop_table(table)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        follow_up_type.drop(bind, checkfirst=True)
        follow_up_status.drop(bind, checkfirst=True)
