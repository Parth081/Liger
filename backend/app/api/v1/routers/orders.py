"""Cart + order endpoints (API_SPEC §4). Dealer scope comes from the token,
never a parameter (BR-AC-07)."""
from __future__ import annotations

import uuid as uuid_mod
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.deps import (
    Actor,
    Pagination,
    get_actor,
    get_db,
    idempotency_key,
    pagination,
    require,
)
from app.core.exceptions import Forbidden, NotFound, ValidationFailed
from app.modules.credit import gate as credit_gate
from app.modules.customers.models import Customer
from app.modules.orders import service
from app.modules.orders.models import Order, Quotation
from app.modules.pricing.sqft import feet_inches_to_inches

router = APIRouter(tags=["orders"])


# ---------------- shared helpers ----------------
def _resolve_customer_id(db: Session, actor: Actor, customer_uid: str | None) -> int:
    """Dealer: always the token's customer (BR-AC-07). Staff: explicit target."""
    if actor.is_dealer:
        if actor.customer_id is None:
            raise Forbidden("Your login is not linked to a customer account yet")
        return actor.customer_id
    if not customer_uid:
        raise ValidationFailed("customer_uid is required when staff place an order")
    customer = db.query(Customer).filter(Customer.uid == uuid_mod.UUID(customer_uid)).first()
    if customer is None:
        raise NotFound("Customer not found")
    if actor.role == "sales_rep" and customer.sales_rep_id != actor.id:
        raise Forbidden("This customer is not assigned to you")  # BR-AC-04
    return customer.id


class CartItemIn(BaseModel):
    design_no: str
    length_ft: Decimal = Field(ge=0)
    length_in: Decimal = Field(default=Decimal(0), ge=0, lt=12)
    breadth_ft: Decimal = Field(ge=0)
    breadth_in: Decimal = Field(default=Decimal(0), ge=0, lt=12)
    quantity: int = Field(ge=1, le=999)
    room_label: str | None = Field(default=None, max_length=60)
    line_discount_paise: int = Field(default=0, ge=0)


class OrderCreateIn(BaseModel):
    customer_uid: str | None = None            # staff on-behalf (BR-ORD-08)
    items: list[CartItemIn] = Field(min_length=1, max_length=200)
    order_discount_paise: int = Field(default=0, ge=0)
    freight_paise: int = Field(default=0, ge=0)
    packing_paise: int = Field(default=0, ge=0)
    expected_delivery_date: date | None = None
    site_name: str | None = Field(default=None, max_length=120)
    remarks: str | None = Field(default=None, max_length=500)
    is_prepaid: bool = False


def _items_payload(items: list[CartItemIn]) -> list[dict]:
    return [
        {
            "design_no": i.design_no,
            "length_in": feet_inches_to_inches(i.length_ft, i.length_in),
            "breadth_in": feet_inches_to_inches(i.breadth_ft, i.breadth_in),
            "quantity": i.quantity,
            "line_discount_paise": i.line_discount_paise,
        }
        for i in items
    ]


def _order_payload(o: Order, full: bool = False) -> dict:
    body = {
        "uid": str(o.uid),
        "order_no": o.order_no,
        "status": o.status,
        "order_date": str(o.order_date),
        "grand_total_paise": o.grand_total_paise,
        "channel": o.channel,
        "is_prepaid": o.is_prepaid,
    }
    if full:
        body.update({
            "subtotal_paise": o.subtotal_paise,
            "order_discount_paise": o.order_discount_paise,
            "taxable_paise": o.taxable_paise,
            "cgst_paise": o.cgst_paise,
            "sgst_paise": o.sgst_paise,
            "igst_paise": o.igst_paise,
            "freight_paise": o.freight_paise,
            "packing_paise": o.packing_paise,
            "round_off_paise": o.round_off_paise,
            "credit_decision": o.credit_decision,
            "site_name": o.site_name,
            "remarks": o.remarks,
            "expected_delivery_date": str(o.expected_delivery_date) if o.expected_delivery_date else None,
            "items": [
                {
                    "design_no": i.design_no, "design_name": i.design_name,
                    "room_label": i.room_label,
                    "length_in": float(i.length_in), "breadth_in": float(i.breadth_in),
                    "quantity": i.quantity,
                    "raw_sqft": float(i.raw_sqft), "billable_sqft": float(i.billable_sqft),
                    "min_rule_applied": i.min_rule_applied,   # BR-SQFT-07
                    "line_area": float(i.line_area),
                    "rate_paise": i.rate_paise, "rate_source": i.rate_source,
                    "line_total_paise": i.line_total_paise,
                }
                for i in o.items
            ],
            "status_history": [
                {"from": h.from_status, "to": h.to_status, "at": h.created_at.isoformat(),
                 "reason": h.reason}
                for h in o.status_history
            ],
        })
    return body


# ---------------- orders ----------------
@router.post("/orders", status_code=201)
def create_order(
    body: OrderCreateIn,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require("order.create", "customer")),
    idem_key: str = Depends(idempotency_key),
):
    customer_id = _resolve_customer_id(db, actor, body.customer_uid)
    order, replay = service.create_order(
        db,
        customer_id=customer_id,
        items=_items_payload(body.items),
        actor_type=actor.type,
        actor_id=actor.id,
        channel="web" if actor.is_dealer else "staff",
        idempotency_key=idem_key,
        order_discount_paise=body.order_discount_paise,
        freight_paise=body.freight_paise,
        packing_paise=body.packing_paise,
        expected_delivery_date=body.expected_delivery_date,
        site_name=body.site_name,
        remarks=body.remarks,
        room_labels=[i.room_label for i in body.items],
        is_prepaid=body.is_prepaid,
        is_sales_rep=(actor.role == "sales_rep"),
    )
    if replay is not None:
        return replay["body"]
    assert order is not None
    return _order_payload(order)


@router.get("/orders")
def list_orders(
    status: str | None = Query(None),
    customer_uid: str | None = Query(None),
    page: Pagination = Depends(pagination),
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
):
    query = db.query(Order)
    if actor.is_dealer:
        query = query.filter(Order.customer_id == actor.customer_id)  # BR-AC-07
    elif customer_uid:
        customer = db.query(Customer).filter(Customer.uid == uuid_mod.UUID(customer_uid)).first()
        if customer is None:
            raise NotFound("Customer not found")
        query = query.filter(Order.customer_id == customer.id)
    if actor.role == "sales_rep":
        query = query.join(Customer, Customer.id == Order.customer_id).filter(
            Customer.sales_rep_id == actor.id
        )  # BR-AC-04
    if status:
        query = query.filter(Order.status == status)
    if page.cursor is not None:
        query = query.filter(Order.id < page.cursor)
    rows = query.order_by(Order.id.desc()).limit(page.limit + 1).all()  # R13
    next_cursor = str(rows[page.limit - 1].id) if len(rows) > page.limit else None
    return {"items": [_order_payload(o) for o in rows[: page.limit]], "next_cursor": next_cursor}


@router.get("/orders/{order_uid}")
def get_order(order_uid: str, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
    order = db.query(Order).filter(Order.uid == uuid_mod.UUID(order_uid)).first()
    if order is None:
        raise NotFound("Order not found")
    if actor.is_dealer and order.customer_id != actor.customer_id:
        raise NotFound("Order not found")   # BR-AC-07: existence not revealed
    if actor.role == "sales_rep":
        customer = db.get(Customer, order.customer_id)
        if customer is None or customer.sales_rep_id != actor.id:
            raise NotFound("Order not found")
    # BR-AC-05: production sees specs, not money — strip money fields
    if actor.role == "production":
        body = _order_payload(order, full=True)
        for key in list(body):
            if key.endswith("_paise") or key == "credit_decision":
                body.pop(key)
        for item in body["items"]:
            for key in list(item):
                if key.endswith("_paise") or key == "rate_source":
                    item.pop(key)
        return body
    return _order_payload(order, full=True)


class StatusIn(BaseModel):
    to_status: str
    reason: str | None = Field(default=None, max_length=255)


@router.post("/orders/{order_uid}/status")
def change_status(order_uid: str, body: StatusIn, db: Session = Depends(get_db),
                  actor: Actor = Depends(get_actor)):
    order = db.query(Order).filter(Order.uid == uuid_mod.UUID(order_uid)).first()
    if order is None:
        raise NotFound("Order not found")
    service.change_status(db, order, body.to_status, actor_type=actor.type,
                          actor_id=actor.id, actor_role=actor.role, reason=body.reason)
    return {"order_no": order.order_no, "status": order.status}


class CancelIn(BaseModel):
    reason: str = Field(min_length=3, max_length=255)


@router.post("/orders/{order_uid}/cancel")
def cancel_order(order_uid: str, body: CancelIn, db: Session = Depends(get_db),
                 actor: Actor = Depends(require("order.cancel"))):
    order = db.query(Order).filter(Order.uid == uuid_mod.UUID(order_uid)).first()
    if order is None:
        raise NotFound("Order not found")
    service.cancel_order(db, order, actor_id=actor.id, actor_role=actor.role, reason=body.reason)
    return {"order_no": order.order_no, "status": order.status}


@router.post("/orders/{order_uid}/approve")
def approve_order(order_uid: str, db: Session = Depends(get_db),
                  actor: Actor = Depends(require("order.approve"))):
    order = db.query(Order).filter(Order.uid == uuid_mod.UUID(order_uid)).first()
    if order is None:
        raise NotFound("Order not found")
    service.approve_order(db, order, actor_id=actor.id, actor_role=actor.role)
    return {"order_no": order.order_no, "status": order.status}


# ---------------- cart ----------------
@router.get("/cart")
def get_cart(customer_uid: str | None = Query(None), db: Session = Depends(get_db),
             actor: Actor = Depends(get_actor)):
    customer_id = _resolve_customer_id(db, actor, customer_uid) if (customer_uid or actor.is_dealer) else None
    cart = service.get_or_create_cart(db, actor.type, actor.id, customer_id)
    return {
        "items": [
            {"uid": str(i.uid), "design_no": i.design_no,
             "length_in": float(i.length_in), "breadth_in": float(i.breadth_in),
             "quantity": i.quantity, "room_label": i.room_label}
            for i in cart.items
        ]
    }


@router.post("/cart/items", status_code=201)
def add_cart_item(body: CartItemIn, customer_uid: str | None = Query(None),
                  db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
    from app.modules.orders.models import CartItem
    from app.modules.pricing.service import get_design_ci

    customer_id = _resolve_customer_id(db, actor, customer_uid) if (customer_uid or actor.is_dealer) else None
    cart = service.get_or_create_cart(db, actor.type, actor.id, customer_id)
    get_design_ci(db, body.design_no)  # BR-CAT-07: fail fast on unknown design
    item = CartItem(
        cart_id=cart.id,
        design_no=body.design_no,
        length_in=feet_inches_to_inches(body.length_ft, body.length_in),
        breadth_in=feet_inches_to_inches(body.breadth_ft, body.breadth_in),
        quantity=body.quantity,
        room_label=body.room_label,
        line_discount_paise=body.line_discount_paise,
    )
    db.add(item)
    db.commit()
    return {"uid": str(item.uid)}


@router.delete("/cart")
def clear_cart(customer_uid: str | None = Query(None), db: Session = Depends(get_db),
               actor: Actor = Depends(get_actor)):
    customer_id = _resolve_customer_id(db, actor, customer_uid) if (customer_uid or actor.is_dealer) else None
    cart = service.get_or_create_cart(db, actor.type, actor.id, customer_id)
    cart.items.clear()
    db.commit()
    return {"ok": True}


@router.get("/cart/summary")
def cart_summary(customer_uid: str | None = Query(None),
                 order_discount_paise: int = Query(0, ge=0),
                 db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
    """Totals + live credit evaluation — drives the order screen's credit strip."""
    customer_id = _resolve_customer_id(db, actor, customer_uid) if (customer_uid or actor.is_dealer) else None
    cart = service.get_or_create_cart(db, actor.type, actor.id, customer_id)
    if not cart.items:
        return {"empty": True, "items": 0}
    priced = service.price_cart_for(db, cart, order_discount_paise=order_discount_paise,
                                    is_sales_rep=(actor.role == "sales_rep"))
    result: dict = {
        "empty": False,
        "items": len(cart.items),
        "subtotal_paise": priced.subtotal.paise,
        "taxable_paise": priced.taxable_total.paise,
        "grand_total_paise": priced.grand_total.paise,
        "needs_approval": priced.needs_approval,
    }
    if cart.customer_id:
        customer = db.get(Customer, cart.customer_id)
        if customer is not None:
            from app.core.money import Money

            decision = credit_gate.evaluate(db, customer, Money(priced.grand_total.paise))
            result["credit"] = decision.as_json()       # BR-CR-21/22
    return result


# ---------------- quotations (BR-ORD-05) ----------------
class QuotationCreateIn(BaseModel):
    customer_uid: str | None = None
    items: list[CartItemIn] = Field(min_length=1, max_length=200)


@router.post("/quotations", status_code=201)
def create_quotation(body: QuotationCreateIn, db: Session = Depends(get_db),
                     actor: Actor = Depends(require("order.create", "customer"))):
    customer_id = _resolve_customer_id(db, actor, body.customer_uid)
    quote = service.create_quotation(db, customer_id=customer_id,
                                     items=_items_payload(body.items),
                                     actor_type=actor.type, actor_id=actor.id)
    return {"uid": str(quote.uid), "quote_no": quote.quote_no,
            "grand_total_paise": quote.grand_total_paise}


@router.post("/quotations/{quote_uid}/convert", status_code=201)
def convert_quotation(quote_uid: str, db: Session = Depends(get_db),
                      actor: Actor = Depends(require("order.create", "customer")),
                      idem_key: str = Depends(idempotency_key)):
    quote = db.query(Quotation).filter(Quotation.uid == uuid_mod.UUID(quote_uid)).first()
    if quote is None:
        raise NotFound("Quotation not found")
    if actor.is_dealer and quote.customer_id != actor.customer_id:
        raise NotFound("Quotation not found")
    order, replay = service.convert_quotation(db, quote, actor_type=actor.type,
                                              actor_id=actor.id,
                                              channel="web" if actor.is_dealer else "staff",
                                              idempotency_key=idem_key)
    if replay is not None:
        return replay["body"]
    assert order is not None
    return _order_payload(order)
