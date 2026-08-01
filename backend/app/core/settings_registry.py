"""Typed, audited access to the settings table (R8).

Every business rule value is read through here. Writes record history and
invalidate the per-process cache. Seed values below are the resolved DEC-*
defaults from BUSINESS_RULES.md §0.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.modules.admin.models import Setting, SettingHistory

# key -> (default, value_type, group, description)
SEED: dict[str, tuple[str, str, str, str]] = {
    "min_billable_sqft": ("11.00", "decimal", "pricing", "BR-SQFT-03 minimum billable sq.ft per piece"),
    "sqft_rounding_step": ("0.25", "decimal", "pricing", "BR-SQFT-05 round billable sqft UP; 0 disables"),
    "max_dimension_in": ("600", "int", "pricing", "BR-SQFT-09 typo guard, inches"),
    "max_rep_discount_pct": ("5", "decimal", "pricing", "BR-PR-07 sales-rep discount cap %"),
    "credit_days_default": ("30", "int", "credit", "BR-CR-03 / DEC-06"),
    "hard_block_days": ("45", "int", "credit", "BR-CR-11 / DEC-07"),
    "ladder_pre_due": ("-3", "int", "credit", "BR-CR-41 days vs due date"),
    "ladder_warn1": ("3", "int", "credit", "BR-CR-43"),
    "ladder_warn2": ("10", "int", "credit", "BR-CR-44"),
    "ladder_block": ("15", "int", "credit", "BR-CR-45 auto-block day"),
    "warn_utilisation_pct": ("80", "decimal", "credit", "BR-CR-14"),
    "cash_bonus_pct_default": ("10", "decimal", "credit", "BR-CR-06 / DEC-08"),
    "cash_ratio_threshold": ("0.30", "decimal", "credit", "BR-CR-06 trailing-6-month confirmed-cash ratio"),
    "credit_enforcement_mode": ("shadow", "str", "credit", "BR-CR-40 shadow|enforce — P9 owner decision"),
    "overdue_soft_block": ("false", "bool", "credit", "BR-CR-15 flip ALLOW+WARN to BLOCK"),
    "global_limit_ceiling_paise": ("100000000000", "int", "credit", "BR-SCR-04 cap on suggested limits (₹10 crore)"),
    "quiet_hours_start": ("21:00", "str", "notifications", "BR-NOT-05 IST"),
    "quiet_hours_end": ("08:00", "str", "notifications", "BR-NOT-05 IST"),
    "max_msgs_per_customer_per_day": ("4", "int", "notifications", "BR-NOT-06"),
    "max_page_size": ("100", "int", "api", "R13 pagination cap"),
    "liger_state": ("GJ", "str", "tax", "BR-TAX-02 place-of-supply comparison"),
    "score_w_punctuality": ("30", "int", "scoring", "BR-SCR-02"),
    "score_w_overdue": ("20", "int", "scoring", "BR-SCR-02"),
    "score_w_volume": ("20", "int", "scoring", "BR-SCR-02"),
    "score_w_consistency": ("10", "int", "scoring", "BR-SCR-02"),
    "score_w_cash": ("10", "int", "scoring", "BR-SCR-02"),
    "score_w_tenure": ("5", "int", "scoring", "BR-SCR-02"),
    "score_w_disputes": ("5", "int", "scoring", "BR-SCR-02"),
}

_cache: dict[str, str] = {}


def seed_settings(db: Session) -> None:
    """Idempotent — inserts only missing keys."""
    existing = {s.key for s in db.query(Setting.key).all()}
    for key, (value, vtype, group, desc) in SEED.items():
        if key not in existing:
            db.add(Setting(key=key, value=value, value_type=vtype, group=group, description=desc))
    db.commit()
    _cache.clear()


def _get_raw(db: Session, key: str) -> str:
    if key in _cache:
        return _cache[key]
    row = db.query(Setting).filter(Setting.key == key).first()
    if row is not None:
        _cache[key] = row.value
        return row.value
    if key in SEED:
        return SEED[key][0]
    raise KeyError(f"Unknown setting '{key}' — add it to settings_registry.SEED (R8)")


def get_str(db: Session, key: str) -> str:
    return _get_raw(db, key)


def get_int(db: Session, key: str) -> int:
    return int(_get_raw(db, key))


def get_decimal(db: Session, key: str) -> Decimal:
    return Decimal(_get_raw(db, key))


def get_bool(db: Session, key: str) -> bool:
    return _get_raw(db, key).strip().lower() in ("true", "1", "yes")


def set_value(db: Session, key: str, value: str, actor_id: int | None) -> None:
    """Audited write (R8): history row + cache invalidation."""
    row = db.query(Setting).filter(Setting.key == key).first()
    old = row.value if row else None
    if row is None:
        if key not in SEED:
            raise KeyError(f"Unknown setting '{key}'")
        _, vtype, group, desc = SEED[key]
        row = Setting(key=key, value=value, value_type=vtype, group=group, description=desc)
        db.add(row)
    else:
        row.value = value
        row.updated_by = actor_id
    db.add(SettingHistory(key=key, old_value=old, new_value=value, changed_by=actor_id, changed_at=utcnow()))
    db.commit()
    _cache.pop(key, None)


def clear_cache() -> None:
    _cache.clear()
