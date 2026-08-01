from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime
from app import models
from app.pricing import calculate_item_pricing  # re-exported so existing callers of engines don't break

__all__ = ["calculate_item_pricing", "compute_customer_due", "evaluate_credit_eligibility", "apply_credit_status"]


def get_setting(db: Session, key: str, default: str) -> float:
    setting = db.query(models.Setting).filter(models.Setting.key == key).first()
    return float(setting.value) if setting else float(default)


def compute_customer_due(db: Session, customer: models.Customer) -> float:
    """The customer's real outstanding due, derived from the ledger — never a
    single mutable field that can drift out of sync with reality."""
    confirmed_order_total = (
        db.query(func.coalesce(func.sum(models.Order.total_amount), 0.0))
        .filter(
            models.Order.customer_id == customer.id,
            models.Order.status != models.OrderStatus.cancelled,
            models.Order.status != models.OrderStatus.draft,
        )
        .scalar()
    )
    confirmed_payment_total = (
        db.query(func.coalesce(func.sum(models.Payment.amount), 0.0))
        .filter(
            models.Payment.customer_id == customer.id,
            models.Payment.status == models.PaymentStatus.confirmed,
        )
        .scalar()
    )
    return round(customer.opening_due + confirmed_order_total - confirmed_payment_total, 2)


def _effective_credit_limit(db: Session, customer: models.Customer, is_cash_payment: bool) -> float:
    limit = customer.credit_limit
    if is_cash_payment and customer.cash_bonus_pct:
        limit += customer.credit_limit * (customer.cash_bonus_pct / 100)
    return limit


def evaluate_credit_eligibility(
    db: Session, customer_id: int, new_order_amount: float, is_cash_payment: bool = False
) -> dict:
    """R1 (due block) + R2 (limit check) + R5 (cash bonus). Called at checkout —
    this is the function that MUST be wired into order creation, not just defined."""
    customer = db.query(models.Customer).filter(models.Customer.id == customer_id).first()
    if not customer:
        return {"allowed": False, "reason": "CUSTOMER_NOT_FOUND"}

    # R1: hard block — status is a persisted field maintained by apply_credit_status(),
    # which runs after every order/payment so this check stays O(1).
    if customer.status == models.CustomerStatus.blocked:
        due = compute_customer_due(db, customer)
        return {
            "allowed": False,
            "reason": "DUE_BLOCK",
            "message": f"Please clear your previous due of ₹{due} to place new orders.",
            "outstanding_due": due,
        }

    total_outstanding = compute_customer_due(db, customer)
    effective_limit = _effective_credit_limit(db, customer, is_cash_payment)
    available_credit = round(effective_limit - total_outstanding, 2)

    # R2: limit check
    if new_order_amount > available_credit:
        return {
            "allowed": False,
            "reason": "LIMIT_EXCEEDED",
            "message": f"Order exceeds available credit. Available: ₹{available_credit}",
            "available_credit": available_credit,
            "outstanding_due": total_outstanding,
        }

    return {
        "allowed": True,
        "available_credit": available_credit,
        "outstanding_due": total_outstanding,
    }


def apply_credit_status(db: Session, customer: models.Customer, actor_admin_id: int = None) -> models.Customer:
    """R3/R4: recompute utilization and move the customer through
    active -> warned -> blocked, logging every transition. Call this after
    every order creation and every payment confirmation."""
    due = compute_customer_due(db, customer)
    utilization = (due / customer.credit_limit) if customer.credit_limit else 0
    previous_status = customer.status

    if utilization >= 1.0:
        new_status = models.CustomerStatus.blocked
    elif utilization >= 0.8:
        new_status = models.CustomerStatus.warned
    else:
        new_status = models.CustomerStatus.active

    if new_status != previous_status:
        customer.status = new_status
        if new_status == models.CustomerStatus.blocked:
            customer.blocked_at = datetime.utcnow()
            customer.block_reason = f"Outstanding due ₹{due} reached/exceeded credit limit ₹{customer.credit_limit}"
        elif previous_status == models.CustomerStatus.blocked and new_status != models.CustomerStatus.blocked:
            customer.unblocked_at = datetime.utcnow()

        db.add(models.CreditEvent(
            customer_id=customer.id,
            event_type=new_status.value,
            reason=f"utilization={utilization:.2%}, due=₹{due}",
            actor_admin_id=actor_admin_id,
        ))
        db.commit()
        db.refresh(customer)
        # NOTE: Phase 1 hooks a notification job onto this exact transition
        # (warned -> notify once, blocked -> notify + stop taking orders).

    return customer
