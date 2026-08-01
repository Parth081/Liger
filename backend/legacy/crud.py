from sqlalchemy.orm import Session
from sqlalchemy import func
from app import models, schemas, engines
from app.pricing import calculate_item_pricing
from datetime import datetime


class CreditBlockedError(Exception):
    """Raised when an order is rejected by the credit engine. api.py maps this to HTTP 403."""
    def __init__(self, detail: dict):
        self.detail = detail
        super().__init__(detail.get("message", "Order blocked by credit engine"))


# ---------------- Design CRUD ----------------
def get_design_by_no(db: Session, design_no: str):
    return db.query(models.Design).filter(models.Design.design_no == design_no).first()


def create_design(db: Session, design: schemas.DesignCreate):
    db_design = models.Design(**design.dict())
    db.add(db_design)
    db.commit()
    db.refresh(db_design)
    return db_design


# ---------------- Customer CRUD ----------------
def get_customer(db: Session, customer_id: int):
    return db.query(models.Customer).filter(models.Customer.id == customer_id).first()


def create_customer(db: Session, customer: schemas.CustomerCreate):
    db_customer = models.Customer(**customer.dict())
    db.add(db_customer)
    db.commit()
    db.refresh(db_customer)
    return db_customer


# ---------------- Order CRUD ----------------
def create_order(db: Session, order: schemas.OrderCreate, customer_id: int, is_cash_payment: bool = False):
    """Builds line items via the single pricing engine, THEN runs the credit
    check before anything is committed. Previously this function never called
    the credit engine at all — any customer could order past their limit."""
    if not customer_id:
        raise ValueError("customer_id is required to place an order (credit check needs a customer)")

    line_items = []
    total_amount = 0.0

    for item in order.line_items:
        calculation = calculate_item_pricing(db, item.length, item.breadth, item.quantity, item.rate_per_sqft)
        db_item = models.OrderLineItem(
            design_no=item.design_no,
            length=item.length,
            breadth=item.breadth,
            quantity=item.quantity,
            rate_per_sqft=item.rate_per_sqft,
            raw_sqft=calculation["raw_sqft"],
            billable_sqft=calculation["billable_sqft"],
            amount=calculation["amount"],
        )
        line_items.append(db_item)
        total_amount += calculation["amount"]

    total_amount = round(total_amount, 2)

    # --- THE FIX: credit engine is now actually enforced at checkout ---
    eligibility = engines.evaluate_credit_eligibility(db, customer_id, total_amount, is_cash_payment)
    if not eligibility["allowed"]:
        raise CreditBlockedError(eligibility)

    db_order = models.Order(
        customer_id=customer_id,
        status=models.OrderStatus.pending_confirmation,
        total_amount=total_amount,
        line_items=line_items,
    )
    db.add(db_order)
    db.commit()
    db.refresh(db_order)

    # Recompute credit status (may move customer into "warned" after this order)
    customer = get_customer(db, customer_id)
    engines.apply_credit_status(db, customer)

    return db_order


# ---------------- Payment ledger CRUD ----------------
def record_payment(db: Session, customer_id: int, amount: float, method: models.PaymentMethod,
                    order_id: int = None, notes: str = None):
    """Card/netbanking can be auto-confirmed by a gateway webhook (Phase 1).
    Cash ALWAYS starts pending_confirmation — matches the requirement that
    admin must manually confirm cash was actually received."""
    status = models.PaymentStatus.pending_confirmation
    payment = models.Payment(
        customer_id=customer_id,
        order_id=order_id,
        amount=amount,
        method=method,
        status=status,
        notes=notes,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment


def confirm_payment(db: Session, payment_id: int, admin_id: int, approve: bool = True):
    payment = db.query(models.Payment).filter(models.Payment.id == payment_id).first()
    if not payment:
        return None

    payment.status = models.PaymentStatus.confirmed if approve else models.PaymentStatus.rejected
    payment.confirmed_by_admin_id = admin_id
    payment.confirmed_at = datetime.utcnow()
    db.commit()
    db.refresh(payment)

    # Confirming a payment can move the customer back out of "warned"/"blocked"
    customer = get_customer(db, payment.customer_id)
    if customer:
        engines.apply_credit_status(db, customer, actor_admin_id=admin_id)

    return payment


# ---------------- Cart CRUD ----------------
def get_cart_items(db: Session, customer_id: int = None):
    query = db.query(models.Cart)
    if customer_id is not None:
        query = query.filter(models.Cart.customer_id == customer_id)
    return query.all()


def add_to_cart(db: Session, cart_item: schemas.CartItemCreate, customer_id: int = None):
    calculation = calculate_item_pricing(
        db, cart_item.length, cart_item.breadth, cart_item.quantity, cart_item.rate_per_sqft
    )
    db_cart = models.Cart(
        customer_id=customer_id,
        design_no=cart_item.design_no,
        length=cart_item.length,
        breadth=cart_item.breadth,
        quantity=cart_item.quantity,
        rate_per_sqft=cart_item.rate_per_sqft,
        raw_sqft=calculation["raw_sqft"],
        billable_sqft=calculation["billable_sqft"],
        amount=calculation["amount"],
    )
    db.add(db_cart)
    db.commit()
    db.refresh(db_cart)
    return db_cart


def clear_cart(db: Session, customer_id: int = None):
    query = db.query(models.Cart)
    if customer_id is not None:
        query = query.filter(models.Cart.customer_id == customer_id)
    query.delete()
    db.commit()
    return True


def get_cart_total(db: Session, customer_id: int = None):
    query = db.query(func.sum(models.Cart.amount))
    if customer_id is not None:
        query = query.filter(models.Cart.customer_id == customer_id)
    result = query.scalar()
    return result or 0
