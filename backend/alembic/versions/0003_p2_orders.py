"""P2: carts, quotations, orders with frozen snapshots, status history.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-01
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")
_BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")

order_status = sa.Enum(
    "DRAFT", "PENDING_APPROVAL", "CONFIRMED", "IN_PRODUCTION", "READY",
    "DISPATCHED", "PARTIALLY_DELIVERED", "DELIVERED", "CLOSED",
    "CANCELLED", "ON_HOLD_CREDIT",
    name="order_status",
)


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
        order_status.create(bind, checkfirst=True)

    op.create_table(
        "carts",
        *_std(),
        sa.Column("owner_type", sa.String(20), nullable=False),
        sa.Column("owner_id", _BIGID, nullable=False),
        sa.Column("customer_id", _BIGID, sa.ForeignKey("customers.id"), nullable=True, index=True),
    )
    op.create_index("uq_cart_owner", "carts", ["owner_type", "owner_id"], unique=True)

    op.create_table(
        "cart_items",
        *_std(),
        sa.Column("cart_id", _BIGID, sa.ForeignKey("carts.id"), nullable=False, index=True),
        sa.Column("design_no", sa.String(50), nullable=False),
        sa.Column("length_in", sa.Numeric(10, 2), nullable=False),
        sa.Column("breadth_in", sa.Numeric(10, 2), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("room_label", sa.String(60), nullable=True),
        sa.Column("line_discount_paise", sa.BigInteger(), nullable=False, server_default="0"),
    )

    op.create_table(
        "orders",
        *_std(),
        sa.Column("created_by", _BIGID, nullable=True),
        sa.Column("updated_by", _BIGID, nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("order_no", sa.String(30), nullable=False, unique=True),
        sa.Column("customer_id", _BIGID, sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("placed_by_type", sa.String(20), nullable=False),
        sa.Column("placed_by_id", _BIGID, nullable=False),
        sa.Column("channel", sa.String(10), nullable=False, server_default="web"),
        sa.Column("status", order_status, nullable=False, server_default="CONFIRMED"),
        sa.Column("order_date", sa.Date(), nullable=False),
        sa.Column("expected_delivery_date", sa.Date(), nullable=True),
        sa.Column("subtotal_paise", sa.BigInteger(), nullable=False),
        sa.Column("order_discount_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("taxable_paise", sa.BigInteger(), nullable=False),
        sa.Column("cgst_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("sgst_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("igst_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("freight_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("packing_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("round_off_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("grand_total_paise", sa.BigInteger(), nullable=False),
        sa.Column("credit_decision", _JSON, nullable=True),
        sa.Column("rate_card_version", sa.Integer(), nullable=True),
        sa.Column("idempotency_key", sa.String(80), nullable=True, unique=True),
        sa.Column("site_name", sa.String(120), nullable=True),
        sa.Column("remarks", sa.String(500), nullable=True),
        sa.Column("is_prepaid", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_orders_customer_date", "orders", ["customer_id", "order_date"])
    op.create_index("ix_orders_status", "orders", ["status"])

    op.create_table(
        "order_items",
        sa.Column("id", _BIGID, primary_key=True, autoincrement=True),
        sa.Column("uid", sa.Uuid(), nullable=False, unique=True, index=True),
        sa.Column("order_id", _BIGID, sa.ForeignKey("orders.id"), nullable=False, index=True),
        sa.Column("design_id", _BIGID, sa.ForeignKey("designs.id"), nullable=True, index=True),
        sa.Column("design_no", sa.String(50), nullable=False),
        sa.Column("design_name", sa.String(200), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("hsn_code", sa.String(10), nullable=True),
        sa.Column("room_label", sa.String(60), nullable=True),
        sa.Column("length_in", sa.Numeric(10, 2), nullable=False),
        sa.Column("breadth_in", sa.Numeric(10, 2), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("raw_sqft", sa.Numeric(10, 2), nullable=False),
        sa.Column("billable_sqft", sa.Numeric(10, 2), nullable=False),
        sa.Column("min_rule_applied", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("line_area", sa.Numeric(12, 2), nullable=False),
        sa.Column("rate_paise", sa.BigInteger(), nullable=False),
        sa.Column("rate_source", sa.String(10), nullable=False),
        sa.Column("making_charge_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("line_discount_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("order_discount_share_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("taxable_paise", sa.BigInteger(), nullable=False),
        sa.Column("gst_pct", sa.Numeric(5, 2), nullable=False),
        sa.Column("cgst_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("sgst_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("igst_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("line_total_paise", sa.BigInteger(), nullable=False),
    )

    op.create_table(
        "order_status_history",
        sa.Column("id", _BIGID, primary_key=True, autoincrement=True),
        sa.Column("uid", sa.Uuid(), nullable=False, unique=True, index=True),
        sa.Column("order_id", _BIGID, sa.ForeignKey("orders.id"), nullable=False, index=True),
        sa.Column("from_status", sa.String(30), nullable=True),
        sa.Column("to_status", sa.String(30), nullable=False),
        sa.Column("actor_type", sa.String(20), nullable=False),
        sa.Column("actor_id", _BIGID, nullable=True),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "quotations",
        *_std(),
        sa.Column("created_by", _BIGID, nullable=True),
        sa.Column("updated_by", _BIGID, nullable=True),
        sa.Column("quote_no", sa.String(30), nullable=False, unique=True),
        sa.Column("customer_id", _BIGID, sa.ForeignKey("customers.id"), nullable=False, index=True),
        sa.Column("status", sa.String(15), nullable=False, server_default="open"),
        sa.Column("grand_total_paise", sa.BigInteger(), nullable=False),
        sa.Column("converted_order_id", _BIGID, sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("payload", _JSON, nullable=False),
    )


def downgrade() -> None:
    for table in ("quotations", "order_status_history", "order_items", "orders",
                  "cart_items", "carts"):
        op.drop_table(table)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        order_status.drop(bind, checkfirst=True)
