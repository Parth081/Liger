"""
Single source of truth for the sq.ft billing calculation.

Previously this logic existed twice (crud.py with a hardcoded MIN_SQ_FT=11,
and engines.py reading it from Settings) and could silently drift apart.
Every caller — live preview, cart, order creation — must go through
calculate_item_pricing() below.
"""
from sqlalchemy.orm import Session
from app import models

DEFAULT_MIN_BILLABLE_SQFT = 11.0


def get_min_billable_sqft(db: Session) -> float:
    setting = db.query(models.Setting).filter(models.Setting.key == "min_billable_sqft").first()
    return float(setting.value) if setting else DEFAULT_MIN_BILLABLE_SQFT


def calculate_item_pricing(db: Session, length: float, breadth: float, quantity: int, rate_per_sqft: float) -> dict:
    if length <= 0 or breadth <= 0:
        raise ValueError("Length and breadth must be greater than 0")
    if quantity <= 0:
        raise ValueError("Quantity must be at least 1")
    if rate_per_sqft < 0:
        raise ValueError("Rate cannot be negative")

    min_sqft = get_min_billable_sqft(db)

    raw_sqft = length * breadth
    billable_sqft = raw_sqft
    min_rule_applied = False

    if raw_sqft < min_sqft:
        billable_sqft = min_sqft
        min_rule_applied = True

    amount = billable_sqft * quantity * rate_per_sqft

    return {
        "raw_sqft": round(raw_sqft, 2),
        "billable_sqft": round(billable_sqft, 2),
        "min_rule_applied": min_rule_applied,
        "amount": round(amount, 2),
    }
