"""P4: payments, allocations, gateway events, payment links, credit notes.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-01
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")
_BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")

payment_status = sa.Enum("initiated", "pending_confirmation", "confirmed", "failed",
                         "reversed", name="payment_status")
payment_method = sa.Enum("upi", "card", "netbanking", "wallet", "cash", "cheque",
                         "bank_transfer", name="payment_method")


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
        payment_status.create(bind, checkfirst=True)
        payment_method.create(bind, checkfirst=True)

    op.create_table(
        "payments",
        *_std(),
        sa.Column("customer_id", _BIGID, sa.ForeignKey("customers.id"), nullable=False, index=True),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("method", payment_method, nullable=False),
        sa.Column("status", payment_status, nullable=False, server_default="initiated"),
        sa.Column("gateway", sa.String(20), nullable=True),
        sa.Column("gateway_order_id", sa.String(80), nullable=True),
        sa.Column("gateway_payment_id", sa.String(80), nullable=True),
        sa.Column("reference_no", sa.String(80), nullable=True),
        sa.Column("slip_url", sa.String(500), nullable=True),
        sa.Column("recorded_by", _BIGID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("confirmed_by", _BIGID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_reason", sa.String(255), nullable=True),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reversal_reason", sa.String(255), nullable=True),
        sa.Column("idempotency_key", sa.String(80), nullable=True, unique=True),
        sa.Column("notes", sa.String(255), nullable=True),
    )
    op.create_index("ix_payments_customer_status", "payments", ["customer_id", "status"])
    op.create_index("ix_payments_gateway_order", "payments", ["gateway_order_id"])

    op.create_table(
        "payment_allocations",
        sa.Column("id", _BIGID, primary_key=True, autoincrement=True),
        sa.Column("uid", sa.Uuid(), nullable=False, unique=True, index=True),
        sa.Column("payment_id", _BIGID, sa.ForeignKey("payments.id"), nullable=False, index=True),
        sa.Column("invoice_id", _BIGID, sa.ForeignKey("invoices.id"), nullable=False, index=True),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "gateway_events",
        sa.Column("id", _BIGID, primary_key=True, autoincrement=True),
        sa.Column("uid", sa.Uuid(), nullable=False, unique=True, index=True),
        sa.Column("gateway", sa.String(20), nullable=False),
        sa.Column("event_id", sa.String(120), nullable=False, unique=True),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("payload", _JSON, nullable=False),
        sa.Column("signature_valid", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "payment_links",
        *_std(),
        sa.Column("customer_id", _BIGID, sa.ForeignKey("customers.id"), nullable=False, index=True),
        sa.Column("invoice_id", _BIGID, sa.ForeignKey("invoices.id"), nullable=True),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("gateway_link_id", sa.String(80), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paid_payment_id", _BIGID, sa.ForeignKey("payments.id"), nullable=True),
    )

    op.create_table(
        "credit_notes",
        *_std(),
        sa.Column("credit_note_no", sa.String(30), nullable=False, unique=True),
        sa.Column("customer_id", _BIGID, sa.ForeignKey("customers.id"), nullable=False, index=True),
        sa.Column("invoice_id", _BIGID, sa.ForeignKey("invoices.id"), nullable=False, index=True),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(255), nullable=False),
        sa.Column("created_by", _BIGID, sa.ForeignKey("users.id"), nullable=True),
    )


def downgrade() -> None:
    for table in ("credit_notes", "payment_links", "gateway_events",
                  "payment_allocations", "payments"):
        op.drop_table(table)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        payment_method.drop(bind, checkfirst=True)
        payment_status.drop(bind, checkfirst=True)
