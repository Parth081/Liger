"""Pricing service — composes sqft + rate + making charge + discount + tax.

price_line() / price_cart() are THE pricing path for preview, cart, quotation,
order and invoice (BR-SQFT-11, BR-PR-11). All money is Money/paise (R1); every
stored step rounds HALF_UP (BR-PR-10).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import settings_registry
from app.core.exceptions import DesignNotFound, ValidationFailed
from app.core.money import Money
from app.modules.catalog.models import Design
from app.modules.pricing import sqft as sqft_engine
from app.modules.pricing import tax as tax_engine
from app.modules.pricing.models import MakingCharge
from app.modules.pricing.rate_resolver import resolve_rate


@dataclass(frozen=True)
class PricedLine:
    """Frozen snapshot fields for order_items (BR-SQFT-10, R9)."""

    design_id: int
    design_no: str
    design_name: str
    category: str
    hsn_code: str | None
    length_in: Decimal
    breadth_in: Decimal
    quantity: int
    raw_sqft: Decimal
    billable_sqft: Decimal
    min_rule_applied: bool
    line_area: Decimal
    rate_paise: int
    rate_source: str
    rate_card_version: int | None
    goods_amount: Money
    making_charge: Money
    line_discount: Money
    taxable: Money              # before order-level discount apportionment
    gst_pct: Decimal
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PricedCart:
    lines: list[PricedLine]
    line_taxes: list[tax_engine.TaxResult]     # after order-discount apportionment
    line_discount_shares: list[Money]          # order discount apportioned per line (BR-PR-09)
    subtotal: Money                            # Σ goods + making − line discounts
    order_discount: Money
    taxable_total: Money
    cgst: Money
    sgst: Money
    igst: Money
    freight: Money
    packing: Money
    round_off: Money
    grand_total: Money
    needs_approval: bool                       # BR-PR-07: discount above rep cap
    approval_reasons: list[str]


def get_design_ci(db: Session, design_no: str) -> Design:
    """BR-CAT-01: case-insensitive lookup. BR-CAT-07: explicit not-found."""
    design = (
        db.query(Design)
        .filter(func.lower(Design.design_no) == design_no.strip().lower(),
                Design.deleted_at.is_(None))
        .first()
    )
    if design is None:
        raise DesignNotFound(f"Design '{design_no}' not found", {"design_no": design_no})
    return design


def _making_charge(db: Session, product_type: str, line_area: Decimal, quantity: int) -> Money:
    """BR-PR-06 / DEC-05."""
    mc = (
        db.query(MakingCharge)
        .filter(MakingCharge.product_type == product_type, MakingCharge.is_active.is_(True))
        .first()
    )
    if mc is None or mc.amount_paise == 0:
        return Money.zero()
    if mc.mode == "per_piece":
        return Money(mc.amount_paise) * quantity
    return Money(mc.amount_paise) * line_area


def price_line(
    db: Session,
    *,
    design_no: str,
    length_in: Decimal,
    breadth_in: Decimal,
    quantity: int,
    on: date,
    customer_id: int | None = None,
    line_discount_paise: int = 0,
    is_sales_rep: bool = False,
) -> tuple[PricedLine, list[str]]:
    """Returns (line, approval_reasons). BR-CAT-04: only active designs orderable."""
    design = get_design_ci(db, design_no)
    if design.status != "active":
        raise ValidationFailed(
            f"Design {design.design_no} is {design.status} and cannot be ordered",
            {"design_no": design.design_no, "status": design.status},
        )

    s = sqft_engine.calculate_sqft(db, length_in, breadth_in, quantity)
    rate = resolve_rate(db, design, on, customer_id)

    goods = Money(rate.rate_paise) * s.line_area                       # BR-PR-05
    making = _making_charge(db, design.category.product_type, s.line_area, quantity)

    discount = Money(line_discount_paise)
    if discount.paise < 0:
        raise ValidationFailed("Discount cannot be negative", {"field": "line_discount"})
    if discount > goods + making:
        raise ValidationFailed("Discount exceeds line value", {"field": "line_discount"})

    # BR-PR-07: rep discount cap -> approval, not silent rejection
    approval_reasons: list[str] = []
    if is_sales_rep and discount.paise > 0:
        cap_pct = settings_registry.get_decimal(db, "max_rep_discount_pct")
        cap = (goods + making).percent(cap_pct)
        if discount > cap:
            approval_reasons.append(
                f"Line discount {discount.format_inr()} exceeds rep cap ({cap_pct}%) on {design.design_no}"
            )

    taxable = goods + making - discount                                # BR-PR-08

    line = PricedLine(
        design_id=design.id,
        design_no=design.design_no,
        design_name=design.name,
        category=design.category.name,
        hsn_code=design.hsn_code,
        length_in=length_in,
        breadth_in=breadth_in,
        quantity=quantity,
        raw_sqft=s.raw_sqft,
        billable_sqft=s.billable_sqft,
        min_rule_applied=s.min_rule_applied,
        line_area=s.line_area,
        rate_paise=rate.rate_paise,
        rate_source=rate.rate_source,
        rate_card_version=rate.rate_card_version,
        goods_amount=goods,
        making_charge=making,
        line_discount=discount,
        taxable=taxable,
        gst_pct=design.gst_pct,
        notes=s.notes,
    )
    return line, approval_reasons


def price_cart(
    db: Session,
    *,
    items: list[dict],
    on: date,
    customer_id: int | None = None,
    customer_state: str | None = None,
    order_discount_paise: int = 0,
    freight_paise: int = 0,
    packing_paise: int = 0,
    is_sales_rep: bool = False,
) -> PricedCart:
    """BR-PR-09/10/11. items: [{design_no, length_in, breadth_in, quantity,
    line_discount_paise?}]."""
    if not items:
        raise ValidationFailed("Cart is empty")

    lines: list[PricedLine] = []
    approval_reasons: list[str] = []
    for item in items:
        line, reasons = price_line(
            db,
            design_no=item["design_no"],
            length_in=item["length_in"],
            breadth_in=item["breadth_in"],
            quantity=item["quantity"],
            on=on,
            customer_id=customer_id,
            line_discount_paise=item.get("line_discount_paise", 0),
            is_sales_rep=is_sales_rep,
        )
        lines.append(line)
        approval_reasons.extend(reasons)

    subtotal = Money.zero()
    for ln in lines:
        subtotal = subtotal + ln.taxable

    order_discount = Money(order_discount_paise)
    if order_discount.paise < 0:
        raise ValidationFailed("Order discount cannot be negative")
    if order_discount > subtotal:
        raise ValidationFailed("Order discount exceeds order value")

    # BR-PR-09: apportion order discount pro-rata BEFORE tax, no paise lost
    if order_discount.paise > 0:
        weights = [ln.taxable.paise for ln in lines]
        shares = order_discount.apportion(weights)
    else:
        shares = [Money.zero() for _ in lines]

    intra = tax_engine.is_intra_state(db, customer_state)
    line_taxes: list[tax_engine.TaxResult] = []
    taxable_total = Money.zero()
    cgst = sgst = igst = Money.zero()
    for ln, share in zip(lines, shares, strict=True):
        line_taxable = ln.taxable - share
        t = tax_engine.compute_tax(line_taxable, ln.gst_pct, intra)
        line_taxes.append(t)
        taxable_total = taxable_total + line_taxable
        cgst, sgst, igst = cgst + t.cgst, sgst + t.sgst, igst + t.igst

    freight = Money(freight_paise)
    packing = Money(packing_paise)
    if freight.paise < 0 or packing.paise < 0:
        raise ValidationFailed("Freight/packing cannot be negative")

    # Freight & packing are added outside GST ("freight extra" trade practice).
    # If the CA requires GST on freight, it becomes a taxed pseudo-line — Settings flag, P4.
    pre_round = taxable_total + cgst + sgst + igst + freight + packing
    grand_total, round_off = pre_round.round_to_rupee()                # BR-PR-10

    return PricedCart(
        lines=lines,
        line_taxes=line_taxes,
        line_discount_shares=shares,
        subtotal=subtotal,
        order_discount=order_discount,
        taxable_total=taxable_total,
        cgst=cgst,
        sgst=sgst,
        igst=igst,
        freight=freight,
        packing=packing,
        round_off=round_off,
        grand_total=grand_total,
        needs_approval=bool(approval_reasons),
        approval_reasons=approval_reasons,
    )
