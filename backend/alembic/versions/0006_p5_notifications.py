"""P5: notification templates, notifications, preferences.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-01
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")
_BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")

channel = sa.Enum("whatsapp", "sms", "email", "in_app", name="notification_channel")
notification_status = sa.Enum("queued", "deferred", "sent", "delivered", "read", "failed",
                              "dead", name="notification_status")


def _std() -> list[sa.Column]:
    return [
        sa.Column("id", _BIGID, primary_key=True, autoincrement=True),
        sa.Column("uid", sa.Uuid(), nullable=False, unique=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        channel.create(bind, checkfirst=True)
        notification_status.create(bind, checkfirst=True)

    op.create_table(
        "notification_templates",
        *_std(),
        sa.Column("key", sa.String(60), nullable=False, index=True),
        sa.Column("channel", channel, nullable=False),
        sa.Column("language", sa.String(5), nullable=False, server_default="en"),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("provider_template_id", sa.String(80), nullable=True),
        sa.Column("is_approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_transactional", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("key", "channel", "language"),
    )

    op.create_table(
        "notifications",
        *_std(),
        sa.Column("customer_id", _BIGID, sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("user_id", _BIGID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("recipient", sa.String(120), nullable=False),
        sa.Column("channel", channel, nullable=False),
        sa.Column("template_key", sa.String(60), nullable=False, index=True),
        sa.Column("language", sa.String(5), nullable=False, server_default="en"),
        sa.Column("rendered_body", sa.Text(), nullable=False),
        sa.Column("payload", _JSON, nullable=True),
        sa.Column("dedupe_key", sa.String(160), nullable=False, unique=True),
        sa.Column("status", notification_status, nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provider_msg_id", sa.String(120), nullable=True),
        sa.Column("error", sa.String(255), nullable=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_notifications_customer", "notifications",
                    ["customer_id", "created_at"])
    op.create_index("ix_notifications_status", "notifications", ["status"])
    op.create_index("ix_notifications_provider_msg", "notifications", ["provider_msg_id"])

    op.create_table(
        "notification_preferences",
        *_std(),
        sa.Column("customer_id", _BIGID, sa.ForeignKey("customers.id"), nullable=False,
                  unique=True),
        sa.Column("whatsapp_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sms_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("email_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("marketing_opt_out", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    for table in ("notification_preferences", "notifications", "notification_templates"):
        op.drop_table(table)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        notification_status.drop(bind, checkfirst=True)
        channel.drop(bind, checkfirst=True)
