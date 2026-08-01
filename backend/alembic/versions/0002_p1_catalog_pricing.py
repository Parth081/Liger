"""P1: customers/regions, catalogue, rate cards (no tiers — DEC-03), making charges.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-01
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")
_BIGID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")

customer_status = sa.Enum("active", "warned", "red", "blocked", name="customer_status")
design_status = sa.Enum("active", "discontinued", "out_of_stock", name="design_status")
uom = sa.Enum("sqft", "piece", "rft", name="uom")
rate_card_status = sa.Enum("draft", "published", "archived", name="rate_card_status")
making_mode = sa.Enum("per_sqft", "per_piece", name="making_mode")


def _std() -> list[sa.Column]:
    return [
        sa.Column("id", _BIGID, primary_key=True, autoincrement=True),
        sa.Column("uid", sa.Uuid(), nullable=False, unique=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _actor() -> list[sa.Column]:
    return [
        sa.Column("created_by", _BIGID, nullable=True),
        sa.Column("updated_by", _BIGID, nullable=True),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for enum in (customer_status, design_status, uom, rate_card_status, making_mode):
            enum.create(bind, checkfirst=True)

    op.create_table(
        "regions",
        *_std(),
        sa.Column("parent_id", _BIGID, sa.ForeignKey("regions.id"), nullable=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("code", sa.String(20), nullable=True),
    )

    op.create_table(
        "customers",
        *_std(),
        *_actor(),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("code", sa.String(30), nullable=False, unique=True),
        sa.Column("business_name", sa.String(200), nullable=False),
        sa.Column("legal_name", sa.String(200), nullable=True),
        sa.Column("gstin", sa.String(15), nullable=True),
        sa.Column("pan", sa.String(10), nullable=True),
        sa.Column("primary_phone", sa.String(20), nullable=False, unique=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("region_id", _BIGID, sa.ForeignKey("regions.id"), nullable=True),
        sa.Column("state", sa.String(40), nullable=True),
        sa.Column("city", sa.String(80), nullable=True),
        sa.Column("pincode", sa.String(10), nullable=True),
        sa.Column("distributor_id", _BIGID, sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("sales_rep_id", _BIGID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("credit_limit_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("credit_days", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("cash_bonus_pct", sa.Numeric(5, 2), nullable=False, server_default="10"),
        sa.Column("opening_balance_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", customer_status, nullable=False, server_default="active"),
        sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("block_reason", sa.String(255), nullable=True),
        sa.Column("unblocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_manual_block", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("language", sa.String(5), nullable=False, server_default="en"),
        sa.Column("notes", sa.Text(), nullable=True),
    )
    for col in ("business_name", "region_id", "distributor_id", "sales_rep_id", "status"):
        op.create_index(f"ix_customers_{col}", "customers", [col])

    op.create_table(
        "customer_addresses",
        *_std(),
        sa.Column("customer_id", _BIGID, sa.ForeignKey("customers.id"), nullable=False, index=True),
        sa.Column("type", sa.String(10), nullable=False),
        sa.Column("line1", sa.String(200), nullable=False),
        sa.Column("line2", sa.String(200), nullable=True),
        sa.Column("state", sa.String(40), nullable=True),
        sa.Column("city", sa.String(80), nullable=True),
        sa.Column("pincode", sa.String(10), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "customer_contacts",
        *_std(),
        sa.Column("customer_id", _BIGID, sa.ForeignKey("customers.id"), nullable=False, index=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="owner"),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("whatsapp_opt_in", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("opt_in_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "categories",
        *_std(),
        sa.Column("parent_id", _BIGID, sa.ForeignKey("categories.id"), nullable=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("code", sa.String(30), nullable=False, unique=True),
        sa.Column("product_type", sa.String(30), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.create_table(
        "designs",
        *_std(),
        *_actor(),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("design_no", sa.String(50), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("category_id", _BIGID, sa.ForeignKey("categories.id"), nullable=False, index=True),
        sa.Column("collection", sa.String(100), nullable=True),
        sa.Column("colour", sa.String(60), nullable=True),
        sa.Column("composition", sa.String(120), nullable=True),
        sa.Column("width_of_goods", sa.String(40), nullable=True),
        sa.Column("hsn_code", sa.String(10), nullable=True),
        sa.Column("gst_pct", sa.Numeric(5, 2), nullable=False, server_default="12"),
        sa.Column("base_rate_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("uom", uom, nullable=False, server_default="sqft"),
        sa.Column("cover_image_id", _BIGID, nullable=True),
        sa.Column("status", design_status, nullable=False, server_default="active"),
    )
    # BR-CAT-01: unique case-insensitive design number
    op.create_index("uq_designs_design_no_ci", "designs", [sa.text("lower(design_no)")], unique=True)
    op.create_index("ix_designs_status", "designs", ["status"])

    op.create_table(
        "design_images",
        *_std(),
        sa.Column("design_id", _BIGID, sa.ForeignKey("designs.id"), nullable=False, index=True),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("variants", _JSON, nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("alt_text", sa.String(200), nullable=True),
    )

    op.create_table(
        "accessories",
        *_std(),
        *_actor(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("code", sa.String(30), nullable=False, unique=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("uom", uom, nullable=False, server_default="piece"),
        sa.Column("rate_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("hsn_code", sa.String(10), nullable=True),
        sa.Column("gst_pct", sa.Numeric(5, 2), nullable=False, server_default="18"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.create_table(
        "rate_cards",
        *_std(),
        *_actor(),
        sa.Column("version", sa.Integer(), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("status", rate_card_status, nullable=False, server_default="draft"),
        sa.Column("published_by", _BIGID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_rate_cards_status", "rate_cards", ["status"])

    op.create_table(
        "rate_card_items",
        sa.Column("id", _BIGID, primary_key=True, autoincrement=True),
        sa.Column("uid", sa.Uuid(), nullable=False, unique=True, index=True),
        sa.Column("rate_card_id", _BIGID, sa.ForeignKey("rate_cards.id"), nullable=False, index=True),
        sa.Column("design_id", _BIGID, sa.ForeignKey("designs.id"), nullable=False, index=True),
        sa.Column("rate_paise", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("rate_card_id", "design_id"),
    )

    op.create_table(
        "customer_special_rates",
        *_std(),
        *_actor(),
        sa.Column("customer_id", _BIGID, sa.ForeignKey("customers.id"), nullable=False, index=True),
        sa.Column("design_id", _BIGID, sa.ForeignKey("designs.id"), nullable=False, index=True),
        sa.Column("rate_paise", sa.BigInteger(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("approved_by", _BIGID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "making_charges",
        *_std(),
        sa.Column("product_type", sa.String(30), nullable=False, unique=True),
        sa.Column("mode", making_mode, nullable=False, server_default="per_sqft"),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    # customer_users.customer_id becomes a real FK now that customers exists
    with op.batch_alter_table("customer_users") as batch:
        batch.create_foreign_key("fk_customer_users_customer", "customers",
                                 ["customer_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("customer_users") as batch:
        batch.drop_constraint("fk_customer_users_customer", type_="foreignkey")
    for table in (
        "making_charges", "customer_special_rates", "rate_card_items", "rate_cards",
        "accessories", "design_images", "designs", "categories",
        "customer_contacts", "customer_addresses", "customers", "regions",
    ):
        op.drop_table(table)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for enum in (making_mode, rate_card_status, uom, design_status, customer_status):
            enum.drop(bind, checkfirst=True)
