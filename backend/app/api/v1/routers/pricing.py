"""Pricing endpoints (API_SPEC §3) — live preview + rate-card management."""
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.deps import Actor, get_actor, get_db, require
from app.core.audit import write_audit
from app.core.exceptions import Conflict, NotFound, ValidationFailed
from app.db.base import utcnow
from app.modules.pricing import service
from app.modules.pricing.models import RateCard, RateCardItem
from app.modules.pricing.sqft import feet_inches_to_inches

router = APIRouter(tags=["pricing"])


class LineIn(BaseModel):
    """Accepts feet+inches or decimal feet (BR-SQFT-01)."""

    design_no: str
    length_ft: Decimal = Field(ge=0)
    length_in: Decimal = Field(default=Decimal(0), ge=0, lt=12)
    breadth_ft: Decimal = Field(ge=0)
    breadth_in: Decimal = Field(default=Decimal(0), ge=0, lt=12)
    quantity: int = Field(ge=1, le=999)
    line_discount_paise: int = Field(default=0, ge=0)
    room_label: str | None = None


class CartIn(BaseModel):
    items: list[LineIn] = Field(min_length=1, max_length=200)
    order_discount_paise: int = Field(default=0, ge=0)
    freight_paise: int = Field(default=0, ge=0)
    packing_paise: int = Field(default=0, ge=0)
    customer_state: str | None = None


def _line_payload(line, taxes=None) -> dict:
    body = {
        "design_no": line.design_no,
        "design_name": line.design_name,
        "raw_sqft": float(line.raw_sqft),
        "billable_sqft": float(line.billable_sqft),
        "min_rule_applied": line.min_rule_applied,
        "line_area": float(line.line_area),
        "rate_paise": line.rate_paise,
        "rate_source": line.rate_source,
        "goods_amount_paise": line.goods_amount.paise,
        "making_charge_paise": line.making_charge.paise,
        "line_discount_paise": line.line_discount.paise,
        "taxable_paise": line.taxable.paise,
        "gst_pct": float(line.gst_pct),
        "notes": line.notes,
    }
    if taxes is not None:
        body["cgst_paise"] = taxes.cgst.paise
        body["sgst_paise"] = taxes.sgst.paise
        body["igst_paise"] = taxes.igst.paise
        body["line_total_paise"] = (line.taxable + taxes.total).paise
    return body


@router.post("/pricing/calculate-line")
def calculate_line(body: LineIn, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
    """Live preview — the same engine as cart/order (BR-SQFT-11)."""
    line, _ = service.price_line(
        db,
        design_no=body.design_no,
        length_in=feet_inches_to_inches(body.length_ft, body.length_in),
        breadth_in=feet_inches_to_inches(body.breadth_ft, body.breadth_in),
        quantity=body.quantity,
        on=date.today(),
        customer_id=actor.customer_id,
        line_discount_paise=body.line_discount_paise,
    )
    return _line_payload(line)


@router.post("/pricing/quote-cart")
def quote_cart(body: CartIn, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
    """Whole-cart pricing incl. tax; no persistence, no credit check."""
    cart = service.price_cart(
        db,
        items=[
            {
                "design_no": i.design_no,
                "length_in": feet_inches_to_inches(i.length_ft, i.length_in),
                "breadth_in": feet_inches_to_inches(i.breadth_ft, i.breadth_in),
                "quantity": i.quantity,
                "line_discount_paise": i.line_discount_paise,
            }
            for i in body.items
        ],
        on=date.today(),
        customer_id=actor.customer_id,
        customer_state=body.customer_state,
        order_discount_paise=body.order_discount_paise,
        freight_paise=body.freight_paise,
        packing_paise=body.packing_paise,
        is_sales_rep=(actor.role == "sales_rep"),
    )
    return {
        "lines": [_line_payload(ln, tx) for ln, tx in zip(cart.lines, cart.line_taxes, strict=True)],
        "subtotal_paise": cart.subtotal.paise,
        "order_discount_paise": cart.order_discount.paise,
        "taxable_paise": cart.taxable_total.paise,
        "cgst_paise": cart.cgst.paise,
        "sgst_paise": cart.sgst.paise,
        "igst_paise": cart.igst.paise,
        "freight_paise": cart.freight.paise,
        "packing_paise": cart.packing.paise,
        "round_off_paise": cart.round_off.paise,
        "grand_total_paise": cart.grand_total.paise,
        "needs_approval": cart.needs_approval,
        "approval_reasons": cart.approval_reasons,
    }


# ---------------- rate cards (BR-PR-03) ----------------
class RateCardCreateIn(BaseModel):
    name: str
    items: list[dict] = Field(default_factory=list)  # [{design_id, rate_paise}]


@router.get("/rate-cards")
def list_rate_cards(db: Session = Depends(get_db),
                    actor: Actor = Depends(require("design.write", "ledger.read"))):
    rows = db.query(RateCard).order_by(RateCard.version.desc()).all()
    return {"items": [
        {"uid": str(r.uid), "version": r.version, "name": r.name, "status": r.status,
         "effective_from": str(r.effective_from) if r.effective_from else None,
         "item_count": len(r.items)}
        for r in rows
    ]}


@router.post("/rate-cards", status_code=201)
def create_rate_card(body: RateCardCreateIn, db: Session = Depends(get_db),
                     actor: Actor = Depends(require("design.write"))):
    last = db.query(RateCard).order_by(RateCard.version.desc()).first()
    card = RateCard(version=(last.version + 1 if last else 1), name=body.name,
                    status="draft", created_by=actor.id)
    db.add(card)
    db.flush()
    for item in body.items:
        db.add(RateCardItem(rate_card_id=card.id, design_id=item["design_id"],
                            rate_paise=item["rate_paise"]))
    write_audit(db, actor_type="user", actor_id=actor.id, action="rate_card.create",
                entity_type="rate_card", entity_id=card.version)
    db.commit()
    return {"uid": str(card.uid), "version": card.version, "status": card.status}


@router.post("/rate-cards/{version}/publish")
def publish_rate_card(version: int, db: Session = Depends(get_db),
                      actor: Actor = Depends(require("design.write"))):
    """BR-PR-03: publish sets effective_from; older cards close the day before."""
    card = db.query(RateCard).filter(RateCard.version == version).first()
    if card is None:
        raise NotFound("Rate card not found")
    if card.status == "published":
        raise Conflict("Rate card already published")
    if not card.items:
        raise ValidationFailed("Cannot publish an empty rate card")
    today = date.today()
    for old in db.query(RateCard).filter(RateCard.status == "published").all():
        old.status = "archived"
        old.effective_to = today
    card.status = "published"
    card.effective_from = today
    card.published_by = actor.id
    card.published_at = utcnow()
    write_audit(db, actor_type="user", actor_id=actor.id, action="rate_card.publish",
                entity_type="rate_card", entity_id=card.version)
    db.commit()
    return {"version": card.version, "status": card.status, "effective_from": str(card.effective_from)}
