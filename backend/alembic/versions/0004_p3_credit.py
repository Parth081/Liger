"""P3: append-only ledger (+DB triggers), invoices (minimal), credit
snapshots/events/overrides/scores, escalation state.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-01
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")
_BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")

ledger_entry_type = sa.Enum(
    "opening", "invoice", "payment", "credit_note", "adjustment", "reversal",
    name="ledger_entry_type",
)
invoice_status = sa.Enum("open", "paid", "cancelled", name="invoice_status")


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
        ledger_entry_type.create(bind, checkfirst=True)
        invoice_status.create(bind, checkfirst=True)

    op.create_table(
        "invoices",
        *_std(),
        sa.Column("invoice_no", sa.String(30), nullable=False, unique=True),
        sa.Column("customer_id", _BIGID, sa.ForeignKey("customers.id"), nullable=False, index=True),
        sa.Column("order_id", _BIGID, sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("invoice_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("total_paise", sa.BigInteger(), nullable=False),
        sa.Column("amount_paid_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", invoice_status, nullable=False, server_default="open"),
    )
    op.create_index("ix_invoices_customer_due", "invoices", ["customer_id", "due_date"])
    op.create_index("ix_invoices_status_due", "invoices", ["status", "due_date"])

    op.create_table(
        "ledger_entries",
        sa.Column("id", _BIGID, primary_key=True, autoincrement=True),
        sa.Column("uid", sa.Uuid(), nullable=False, unique=True, index=True),
        sa.Column("customer_id", _BIGID, sa.ForeignKey("customers.id"), nullable=False, index=True),
        sa.Column("entry_type", ledger_entry_type, nullable=False),
        sa.Column("debit_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("credit_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("balance_after_paise", sa.BigInteger(), nullable=False),
        sa.Column("ref_type", sa.String(30), nullable=True),
        sa.Column("ref_id", _BIGID, nullable=True),
        sa.Column("meta", _JSON, nullable=True),
        sa.Column("narration", sa.String(255), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", _BIGID, nullable=True),
    )
    op.create_index("ix_ledger_customer_posted", "ledger_entries", ["customer_id", "posted_at"])

    # R2 / BR-LED-01: append-only enforced by the DATABASE, not by convention.
    if bind.dialect.name == "postgresql":
        op.execute("""
            CREATE OR REPLACE FUNCTION ledger_append_only() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'ledger_entries is append-only (R2): % blocked', TG_OP;
            END; $$ LANGUAGE plpgsql;
        """)
        op.execute("""
            CREATE TRIGGER trg_ledger_no_update BEFORE UPDATE OR DELETE ON ledger_entries
            FOR EACH ROW EXECUTE FUNCTION ledger_append_only();
        """)
    else:  # SQLite — same guarantee for the test environment
        op.execute("""
            CREATE TRIGGER trg_ledger_no_update BEFORE UPDATE ON ledger_entries
            BEGIN SELECT RAISE(ABORT, 'ledger_entries is append-only (R2)'); END;
        """)
        op.execute("""
            CREATE TRIGGER trg_ledger_no_delete BEFORE DELETE ON ledger_entries
            BEGIN SELECT RAISE(ABORT, 'ledger_entries is append-only (R2)'); END;
        """)

    op.create_table(
        "credit_snapshots",
        sa.Column("id", _BIGID, primary_key=True, autoincrement=True),
        sa.Column("uid", sa.Uuid(), nullable=False, unique=True, index=True),
        sa.Column("customer_id", _BIGID, sa.ForeignKey("customers.id"), nullable=False, index=True),
        sa.Column("as_of", sa.Date(), nullable=False, index=True),
        sa.Column("outstanding_paise", sa.BigInteger(), nullable=False),
        sa.Column("exposure_paise", sa.BigInteger(), nullable=False),
        sa.Column("effective_limit_paise", sa.BigInteger(), nullable=False),
        sa.Column("available_paise", sa.BigInteger(), nullable=False),
        sa.Column("overdue_current_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("overdue_1_30_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("overdue_31_60_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("overdue_61_90_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("overdue_90_plus_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("colour", sa.String(10), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.UniqueConstraint("customer_id", "as_of"),
    )

    op.create_table(
        "credit_events",
        sa.Column("id", _BIGID, primary_key=True, autoincrement=True),
        sa.Column("uid", sa.Uuid(), nullable=False, unique=True, index=True),
        sa.Column("customer_id", _BIGID, sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("detail", _JSON, nullable=True),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("actor_type", sa.String(20), nullable=False, server_default="system"),
        sa.Column("actor_id", _BIGID, nullable=True),
        sa.Column("is_shadow", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
    )
    op.create_index("ix_credit_events_customer", "credit_events", ["customer_id", "created_at"])

    op.create_table(
        "credit_overrides",
        *_std(),
        sa.Column("customer_id", _BIGID, sa.ForeignKey("customers.id"), nullable=False, index=True),
        sa.Column("extra_limit_paise", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(255), nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=False),
        sa.Column("granted_by", _BIGID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "customer_scores",
        sa.Column("id", _BIGID, primary_key=True, autoincrement=True),
        sa.Column("uid", sa.Uuid(), nullable=False, unique=True, index=True),
        sa.Column("customer_id", _BIGID, sa.ForeignKey("customers.id"), nullable=False, index=True),
        sa.Column("computed_on", sa.Date(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("band", sa.String(4), nullable=False),
        sa.Column("factors", _JSON, nullable=False),
        sa.Column("suggested_limit_paise", sa.BigInteger(), nullable=True),
        sa.UniqueConstraint("customer_id", "computed_on"),
    )

    op.create_table(
        "escalation_state",
        sa.Column("id", _BIGID, primary_key=True, autoincrement=True),
        sa.Column("uid", sa.Uuid(), nullable=False, unique=True, index=True),
        sa.Column("invoice_id", _BIGID, sa.ForeignKey("invoices.id"), nullable=False, index=True),
        sa.Column("customer_id", _BIGID, sa.ForeignKey("customers.id"), nullable=False, index=True),
        sa.Column("step", sa.String(15), nullable=False),
        sa.Column("fired_on", sa.Date(), nullable=False),
        sa.Column("notified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("invoice_id", "step"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_ledger_no_update ON ledger_entries")
        op.execute("DROP FUNCTION IF EXISTS ledger_append_only()")
    for table in ("escalation_state", "customer_scores", "credit_overrides", "credit_events",
                  "credit_snapshots", "ledger_entries", "invoices"):
        op.drop_table(table)
    if bind.dialect.name == "postgresql":
        invoice_status.drop(bind, checkfirst=True)
        ledger_entry_type.drop(bind, checkfirst=True)
