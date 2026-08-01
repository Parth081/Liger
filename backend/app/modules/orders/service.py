"""Order service — the critical path (ARCHITECTURE §3).

create_order: idempotency → price → LOCK customer → credit gate → persist
snapshots → gapless number → commit → emit events. Slow side effects happen
in workers, never inline (R10).
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.core import events, idempotency
from app.core.audit import write_audit
from app.core.exceptions import (
    CreditBlocked,
    NotFound,
    ValidationFailed,
)
from app.core.numbering import next_number, next_order_no
from app.core.permissions import role_has
from app.db.base import utcnow
from app.modules.credit import gate as credit_gate
from app.modules.customers.models import Customer
from app.modules.orders.models import (
    Cart,
    Order,
    OrderItem,
    OrderStatusHistory,
    Quotation,
)
from app.modules.orders.state_machine import required_permission
from app.modules.pricing.service import PricedCart, price_cart


# ---------------- cart ----------------
def get_or_create_cart(db: Session, owner_type: str, owner_id: int,
                       customer_id: int | None) -> Cart:
    cart = (
        db.query(Cart)
        .filter(Cart.owner_type == owner_type, Cart.owner_id == owner_id)
        .first()
    )
    if cart is None:
        cart = Cart(owner_type=owner_type, owner_id=owner_id, customer_id=customer_id)
        db.add(cart)
        db.commit()
        db.refresh(cart)
    elif customer_id is not None and cart.customer_id != customer_id:
        # staff switched which dealer they are ordering for — cart follows
        cart.customer_id = customer_id
        cart.items.clear()
        db.commit()
    return cart


def cart_items_payload(cart: Cart) -> list[dict[str, Any]]:
    return [
        {
            "design_no": i.design_no,
            "length_in": i.length_in,
            "breadth_in": i.breadth_in,
            "quantity": i.quantity,
            "line_discount_paise": i.line_discount_paise,
        }
        for i in cart.items
    ]


def price_cart_for(db: Session, cart: Cart, *, order_discount_paise: int = 0,
                   freight_paise: int = 0, packing_paise: int = 0,
                   is_sales_rep: bool = False) -> PricedCart:
    customer = db.get(Customer, cart.customer_id) if cart.customer_id else None
    return price_cart(
        db,
        items=cart_items_payload(cart),
        on=date.today(),
        customer_id=cart.customer_id,
        customer_state=customer.state if customer else None,
        order_discount_paise=order_discount_paise,
        freight_paise=freight_paise,
        packing_paise=packing_paise,
        is_sales_rep=is_sales_rep,
    )


# ---------------- order creation (the credit-gated path) ----------------
def create_order(
    db: Session,
    *,
    customer_id: int,
    items: list[dict[str, Any]],
    actor_type: str,
    actor_id: int,
    channel: str,
    idempotency_key: str,
    order_discount_paise: int = 0,
    freight_paise: int = 0,
    packing_paise: int = 0,
    expected_delivery_date: date | None = None,
    site_name: str | None = None,
    remarks: str | None = None,
    room_labels: list[str | None] | None = None,
    is_prepaid: bool = False,
    is_sales_rep: bool = False,
) -> tuple[Order | None, dict[str, Any] | None]:
    """Returns (order, replay_response). Exactly one is non-None.

    BR-ORD-04: the gate runs here at checkout; approval re-runs it.
    """
    # R6 — replay?
    replay = idempotency.begin(db, idempotency_key, "POST /orders", {
        "customer_id": customer_id, "items": items,
        "order_discount_paise": order_discount_paise,
        "freight_paise": freight_paise, "packing_paise": packing_paise,
    })
    if replay is not None:
        return None, replay

    # Price first — cheap to reject an unpriceable cart before locking anything
    customer_probe = db.get(Customer, customer_id)
    if customer_probe is None or customer_probe.deleted_at is not None:
        raise NotFound("Customer not found")
    priced = price_cart(
        db,
        items=items,
        on=date.today(),
        customer_id=customer_id,
        customer_state=customer_probe.state,
        order_discount_paise=order_discount_paise,
        freight_paise=freight_paise,
        packing_paise=packing_paise,
        is_sales_rep=is_sales_rep,
    )

    # R7 / BR-CR-20 — serialize concurrent orders on the customer row
    customer = credit_gate.lock_customer(db, customer_id)
    assert customer is not None  # probed above; lock re-reads within txn

    from app.core.money import Money

    decision = credit_gate.evaluate(db, customer, Money(priced.grand_total.paise),
                                    is_prepaid=is_prepaid)

    if decision.decision == credit_gate.BLOCK:
        db.rollback()
        raise CreditBlocked(
            f"Please clear your outstanding of {Money(decision.outstanding_paise).format_inr()} "
            "to place a new order.",
            decision.as_json(),
        )

    if priced.needs_approval or decision.decision == credit_gate.NEEDS_APPROVAL:
        status = "PENDING_APPROVAL"     # BR-CR-13 / BR-PR-07 — approval, not rejection
    else:
        status = "CONFIRMED"

    order = Order(
        order_no=next_order_no(db, date.today()),       # BR-ORD-06, in-transaction
        customer_id=customer_id,
        placed_by_type=actor_type,
        placed_by_id=actor_id,
        channel=channel,
        status=status,
        order_date=date.today(),
        expected_delivery_date=expected_delivery_date,
        subtotal_paise=priced.subtotal.paise,
        order_discount_paise=priced.order_discount.paise,
        taxable_paise=priced.taxable_total.paise,
        cgst_paise=priced.cgst.paise,
        sgst_paise=priced.sgst.paise,
        igst_paise=priced.igst.paise,
        freight_paise=priced.freight.paise,
        packing_paise=priced.packing.paise,
        round_off_paise=priced.round_off.paise,
        grand_total_paise=priced.grand_total.paise,
        credit_decision=decision.as_json(),             # BR-CR-21 frozen
        rate_card_version=next(
            (ln.rate_card_version for ln in priced.lines if ln.rate_card_version), None
        ),
        idempotency_key=idempotency_key,
        site_name=site_name,
        remarks=remarks,
        is_prepaid=is_prepaid,
        created_by=actor_id if actor_type == "user" else None,
    )
    db.add(order)
    db.flush()

    labels = room_labels or [None] * len(priced.lines)
    for ln, tax, share, label in zip(priced.lines, priced.line_taxes,
                                     priced.line_discount_shares, labels, strict=True):
        db.add(OrderItem(
            order_id=order.id,
            design_id=ln.design_id,
            design_no=ln.design_no,
            design_name=ln.design_name,
            category=ln.category,
            hsn_code=ln.hsn_code,
            room_label=label,
            length_in=ln.length_in,
            breadth_in=ln.breadth_in,
            quantity=ln.quantity,
            raw_sqft=ln.raw_sqft,
            billable_sqft=ln.billable_sqft,
            min_rule_applied=ln.min_rule_applied,
            line_area=ln.line_area,
            rate_paise=ln.rate_paise,
            rate_source=ln.rate_source,
            making_charge_paise=ln.making_charge.paise,
            line_discount_paise=ln.line_discount.paise,
            order_discount_share_paise=share.paise,
            taxable_paise=(ln.taxable - share).paise,
            gst_pct=ln.gst_pct,
            cgst_paise=tax.cgst.paise,
            sgst_paise=tax.sgst.paise,
            igst_paise=tax.igst.paise,
            line_total_paise=(ln.taxable - share + tax.total).paise,
        ))

    db.add(OrderStatusHistory(order_id=order.id, from_status=None, to_status=status,
                              actor_type=actor_type, actor_id=actor_id,
                              reason="; ".join(priced.approval_reasons) or None,
                              created_at=utcnow()))
    write_audit(db, actor_type=actor_type, actor_id=actor_id, action="order.create",
                entity_type="order", entity_id=order.order_no,
                after={"grand_total_paise": order.grand_total_paise, "status": status})

    response = {
        "uid": str(order.uid),
        "order_no": order.order_no,
        "status": order.status,
        "grand_total_paise": order.grand_total_paise,
        "credit_decision": order.credit_decision,
    }
    idempotency.store(db, idempotency_key, 201, response)
    db.commit()

    events.emit("order.placed", {"order_id": order.id, "order_no": order.order_no,
                                 "customer_id": customer_id,
                                 "grand_total_paise": order.grand_total_paise})  # R10
    return order, None


# ---------------- status transitions ----------------
def change_status(db: Session, order: Order, to_status: str, *,
                  actor_type: str, actor_id: int, actor_role: str,
                  reason: str | None = None) -> Order:
    perm = required_permission(order.status, to_status)   # BR-ORD-02
    if perm is not None and actor_type == "user" and not role_has(actor_role, perm):
        from app.core.exceptions import Forbidden

        raise Forbidden(f"Transition to {to_status} requires {perm}")
    if perm is not None and actor_type != "user":
        from app.core.exceptions import Forbidden

        raise Forbidden("Dealers cannot change order status")

    from_status = order.status
    order.status = to_status
    db.add(OrderStatusHistory(order_id=order.id, from_status=from_status, to_status=to_status,
                              actor_type=actor_type, actor_id=actor_id, reason=reason,
                              created_at=utcnow()))
    write_audit(db, actor_type=actor_type, actor_id=actor_id, action="order.status",
                entity_type="order", entity_id=order.order_no,
                before={"status": from_status}, after={"status": to_status, "reason": reason})
    db.commit()
    events.emit("order.status_changed", {"order_id": order.id, "order_no": order.order_no,
                                         "from": from_status, "to": to_status})  # BR-ORD-03
    return order


def cancel_order(db: Session, order: Order, *, actor_id: int, actor_role: str,
                 reason: str) -> Order:
    """BR-ORD-09: admin only, mandatory reason, releases exposure."""
    if not reason or not reason.strip():
        raise ValidationFailed("A cancellation reason is required")
    return change_status(db, order, "CANCELLED", actor_type="user", actor_id=actor_id,
                         actor_role=actor_role, reason=reason.strip())


def approve_order(db: Session, order: Order, *, actor_id: int, actor_role: str) -> Order:
    """BR-ORD-04: approval RE-RUNS the credit gate — things change between
    checkout and approval."""
    from app.core.money import Money

    customer = credit_gate.lock_customer(db, order.customer_id)
    if customer is None:
        raise NotFound("Customer not found")
    decision = credit_gate.evaluate(db, customer, Money(order.grand_total_paise),
                                    is_prepaid=order.is_prepaid)
    if decision.decision == credit_gate.BLOCK:
        db.rollback()
        raise CreditBlocked("Customer is blocked — clear dues before approving this order.",
                            decision.as_json())
    order.credit_decision = decision.as_json()
    return change_status(db, order, "CONFIRMED", actor_type="user", actor_id=actor_id,
                         actor_role=actor_role)


# ---------------- quotations (BR-ORD-05) ----------------
def create_quotation(db: Session, *, customer_id: int, items: list[dict[str, Any]],
                     actor_type: str, actor_id: int) -> Quotation:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise NotFound("Customer not found")
    priced = price_cart(db, items=items, on=date.today(), customer_id=customer_id,
                        customer_state=customer.state)
    n = next_number(db, f"quote/{date.today().year}")
    quote = Quotation(
        quote_no=f"LGR/Q/{date.today().year}/{n:05d}",
        customer_id=customer_id,
        grand_total_paise=priced.grand_total.paise,
        payload={"items": [
            {k: str(v) if hasattr(v, "quantize") else v for k, v in item.items()}
            for item in items
        ]},
        created_by=actor_id if actor_type == "user" else None,
    )
    db.add(quote)
    db.commit()
    db.refresh(quote)
    return quote


def convert_quotation(db: Session, quote: Quotation, *, actor_type: str, actor_id: int,
                      channel: str, idempotency_key: str) -> tuple[Order | None, dict | None]:
    """Runs the FULL credit gate (BR-ORD-05). Prices are re-resolved at
    conversion time — a quote is an estimate, not a rate lock."""
    from decimal import Decimal

    from app.core.exceptions import Conflict

    if quote.status == "converted":
        raise Conflict("Quotation already converted")
    items = [
        {
            "design_no": i["design_no"],
            "length_in": Decimal(i["length_in"]),
            "breadth_in": Decimal(i["breadth_in"]),
            "quantity": int(i["quantity"]),
            "line_discount_paise": int(i.get("line_discount_paise", 0)),
        }
        for i in quote.payload["items"]
    ]
    order, replay = create_order(
        db, customer_id=quote.customer_id, items=items,
        actor_type=actor_type, actor_id=actor_id, channel=channel,
        idempotency_key=idempotency_key,
    )
    if order is not None:
        quote.status = "converted"
        quote.converted_order_id = order.id
        db.commit()
    return order, replay
