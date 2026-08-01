"""Catalogue models (DATA_MODEL §3): categories, designs, images, accessories."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import ActorMixin, Base, PKMixin, SoftDeleteMixin, TimestampMixin, VersionMixin

_FK_INT = BigInteger().with_variant(Integer, "sqlite")
_JSON = JSON().with_variant(JSONB, "postgresql")

DESIGN_STATUS = sa.Enum("active", "discontinued", "out_of_stock", name="design_status")  # BR-CAT-04
UOM = sa.Enum("sqft", "piece", "rft", name="uom")


class Category(Base, PKMixin, TimestampMixin):
    """BR-CAT-03. product_type drives making-charge resolution (BR-PR-06)."""

    __tablename__ = "categories"

    parent_id: Mapped[int | None] = mapped_column(_FK_INT, ForeignKey("categories.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(100))
    code: Mapped[str] = mapped_column(String(30), unique=True)
    product_type: Mapped[str] = mapped_column(String(30))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Design(Base, PKMixin, TimestampMixin, ActorMixin, VersionMixin, SoftDeleteMixin):
    __tablename__ = "designs"
    __table_args__ = (
        # BR-CAT-01: unique case-insensitive
        Index("uq_designs_design_no_ci", func.lower(sa.text("design_no")), unique=True),
    )

    design_no: Mapped[str] = mapped_column(String(50), index=True)
    name: Mapped[str] = mapped_column(String(200))
    category_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("categories.id"), index=True)
    collection: Mapped[str | None] = mapped_column(String(100), nullable=True)
    colour: Mapped[str | None] = mapped_column(String(60), nullable=True)
    composition: Mapped[str | None] = mapped_column(String(120), nullable=True)
    width_of_goods: Mapped[str | None] = mapped_column(String(40), nullable=True)
    hsn_code: Mapped[str | None] = mapped_column(String(10), nullable=True)  # BR-TAX-03
    gst_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("12"))  # DEC-04
    base_rate_paise: Mapped[int] = mapped_column(BigInteger, default=0)  # fallback when no rate card
    uom: Mapped[str] = mapped_column(UOM, default="sqft")
    cover_image_id: Mapped[int | None] = mapped_column(_FK_INT, nullable=True)
    status: Mapped[str] = mapped_column(DESIGN_STATUS, default="active", index=True)

    category: Mapped[Category] = relationship(lazy="joined")
    images: Mapped[list[DesignImage]] = relationship(
        back_populates="design", lazy="selectin", order_by="DesignImage.sort_order"
    )


class DesignImage(Base, PKMixin, TimestampMixin):
    """BR-CAT-05: variants JSON holds thumb/card/zoom URLs."""

    __tablename__ = "design_images"

    design_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("designs.id"), index=True)
    url: Mapped[str] = mapped_column(String(500))
    variants: Mapped[dict[str, Any] | None] = mapped_column(_JSON, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    alt_text: Mapped[str | None] = mapped_column(String(200), nullable=True)

    design: Mapped[Design] = relationship(back_populates="images")


class Accessory(Base, PKMixin, TimestampMixin, ActorMixin, SoftDeleteMixin):
    """BR-CAT-08 / DEC-12: hardware — own UOM, min-sq.ft rule never applies."""

    __tablename__ = "accessories"

    code: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(150))
    uom: Mapped[str] = mapped_column(UOM, default="piece")
    rate_paise: Mapped[int] = mapped_column(BigInteger, default=0)
    hsn_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    gst_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("18"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# BR-CAT-03 defaults, seeded in db/seed.py
DEFAULT_CATEGORIES: list[tuple[str, str, str]] = [
    ("Curtain Fabric", "CURTAIN", "curtain"),
    ("Roller Blind", "ROLLER", "roller"),
    ("Zebra Blind", "ZEBRA", "zebra"),
    ("Roman Blind", "ROMAN", "roman"),
    ("Vertical Blind", "VERTICAL", "vertical"),
    ("Venetian Blind", "VENETIAN", "venetian"),
    ("Wooden Blind", "WOODEN", "wooden"),
    ("Honeycomb Blind", "HONEYCOMB", "honeycomb"),
    ("Sheer", "SHEER", "sheer"),
    ("Blackout", "BLACKOUT", "blackout"),
    ("Accessory", "ACCESSORY", "accessory"),
]
