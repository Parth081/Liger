"""P0 foundations: identity, RBAC, settings, audit, idempotency, numbering.

Revision ID: 0001
Revises:
Create Date: 2026-08-01
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")
_BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def _std_cols() -> list[sa.Column]:
    """id/uid/timestamps shared by every table (DATA_MODEL conventions)."""
    return [
        sa.Column("id", _BIGID, primary_key=True, autoincrement=True),
        sa.Column("uid", sa.Uuid(), nullable=False, unique=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "roles",
        *_std_cols(),
        sa.Column("code", sa.String(40), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.create_table(
        "permissions",
        sa.Column("id", _BIGID, primary_key=True, autoincrement=True),
        sa.Column("uid", sa.Uuid(), nullable=False, unique=True, index=True),
        sa.Column("code", sa.String(80), nullable=False, unique=True),
        sa.Column("description", sa.String(255), nullable=False, server_default=""),
    )

    op.create_table(
        "role_permissions",
        sa.Column("role_id", _BIGID, sa.ForeignKey("roles.id"), primary_key=True),
        sa.Column("permission_id", _BIGID, sa.ForeignKey("permissions.id"), primary_key=True),
    )

    op.create_table(
        "users",
        *_std_cols(),
        sa.Column("created_by", _BIGID, nullable=True),
        sa.Column("updated_by", _BIGID, nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role_id", _BIGID, sa.ForeignKey("roles.id"), nullable=False),
        sa.Column("totp_secret", sa.String(64), nullable=True),
        sa.Column("is_2fa_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_role_id", "users", ["role_id"])

    op.create_table(
        "customer_users",
        *_std_cols(),
        sa.Column("created_by", _BIGID, nullable=True),
        sa.Column("updated_by", _BIGID, nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("customer_id", _BIGID, nullable=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("language", sa.String(5), nullable=False, server_default="en"),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_customer_users_phone", "customer_users", ["phone"], unique=True)
    op.create_index("ix_customer_users_customer_id", "customer_users", ["customer_id"])

    op.create_table(
        "otp_requests",
        *_std_cols(),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("code_hash", sa.String(255), nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False, server_default="login"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
    )
    op.create_index("ix_otp_phone_created", "otp_requests", ["phone", "created_at"])

    op.create_table(
        "refresh_tokens",
        *_std_cols(),
        sa.Column("subject_type", sa.String(20), nullable=False),
        sa.Column("subject_id", _BIGID, nullable=False),
        sa.Column("token_hash", sa.String(255), nullable=False, unique=True),
        sa.Column("device", sa.String(255), nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_refresh_tokens_subject_id", "refresh_tokens", ["subject_id"])

    op.create_table(
        "settings",
        *_std_cols(),
        sa.Column("key", sa.String(80), nullable=False, unique=True, index=True),
        sa.Column("value", sa.String(255), nullable=False),
        sa.Column("value_type", sa.String(10), nullable=False, server_default="str"),
        sa.Column("group", sa.String(40), nullable=False, server_default="general"),
        sa.Column("description", sa.String(255), nullable=False, server_default=""),
        sa.Column("updated_by", _BIGID, nullable=True),
    )

    op.create_table(
        "settings_history",
        sa.Column("id", _BIGID, primary_key=True, autoincrement=True),
        sa.Column("uid", sa.Uuid(), nullable=False, unique=True, index=True),
        sa.Column("key", sa.String(80), nullable=False, index=True),
        sa.Column("old_value", sa.String(255), nullable=True),
        sa.Column("new_value", sa.String(255), nullable=False),
        sa.Column("changed_by", _BIGID, nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", _BIGID, primary_key=True, autoincrement=True),
        sa.Column("uid", sa.Uuid(), nullable=False, unique=True, index=True),
        sa.Column("actor_type", sa.String(20), nullable=False),
        sa.Column("actor_id", _BIGID, nullable=True),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("entity_type", sa.String(60), nullable=False),
        sa.Column("entity_id", sa.String(60), nullable=False),
        sa.Column("before", _JSON, nullable=True),
        sa.Column("after", _JSON, nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
    )
    op.create_index("ix_audit_entity", "audit_log", ["entity_type", "entity_id", "created_at"])

    op.create_table(
        "idempotency_keys",
        *_std_cols(),
        sa.Column("key", sa.String(80), nullable=False, unique=True, index=True),
        sa.Column("endpoint", sa.String(120), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response", _JSON, nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "number_series",
        sa.Column("id", _BIGID, primary_key=True, autoincrement=True),
        sa.Column("uid", sa.Uuid(), nullable=False, unique=True, index=True),
        sa.Column("series", sa.String(40), nullable=False, unique=True),
        sa.Column("last_value", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    for table in (
        "number_series", "idempotency_keys", "audit_log", "settings_history", "settings",
        "refresh_tokens", "otp_requests", "customer_users", "users",
        "role_permissions", "permissions", "roles",
    ):
        op.drop_table(table)
