"""Rate resolution — BR-PR-01/02 (DEC-03: no tiers).

Cascade, first match wins:
  1. active customer special rate for the design      -> source "special"
  2. active published rate card item for the design   -> source "base"
  3. design.base_rate_paise (> 0)                     -> source "base"
  4. RateNotFound — the line is REJECTED, never zero-rated.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.core.exceptions import RateNotFound
from app.modules.catalog.models import Design
from app.modules.pricing.models import CustomerSpecialRate, RateCard, RateCardItem


@dataclass(frozen=True)
class ResolvedRate:
    rate_paise: int
    rate_source: str            # special | base  (BR-PR-02)
    rate_card_version: int | None


def active_rate_card(db: Session, on: date) -> RateCard | None:
    """Latest published card effective on the date (BR-PR-03)."""
    return (
        db.query(RateCard)
        .filter(
            RateCard.status == "published",
            RateCard.effective_from <= on,
            (RateCard.effective_to.is_(None)) | (RateCard.effective_to >= on),
        )
        .order_by(RateCard.version.desc())
        .first()
    )


def resolve_rate(
    db: Session,
    design: Design,
    on: date,
    customer_id: int | None = None,
) -> ResolvedRate:
    # 1. Customer special rate (BR-PR-04: auto-expiring window)
    if customer_id is not None:
        special = (
            db.query(CustomerSpecialRate)
            .filter(
                CustomerSpecialRate.customer_id == customer_id,
                CustomerSpecialRate.design_id == design.id,
                CustomerSpecialRate.revoked.is_(False),
                CustomerSpecialRate.valid_from <= on,
                CustomerSpecialRate.valid_to >= on,
            )
            .order_by(CustomerSpecialRate.id.desc())
            .first()
        )
        if special is not None:
            return ResolvedRate(special.rate_paise, "special", None)

    # 2. Active rate card
    card = active_rate_card(db, on)
    if card is not None:
        item = (
            db.query(RateCardItem)
            .filter(RateCardItem.rate_card_id == card.id, RateCardItem.design_id == design.id)
            .first()
        )
        if item is not None:
            return ResolvedRate(item.rate_paise, "base", card.version)

    # 3. Design base rate
    if design.base_rate_paise and design.base_rate_paise > 0:
        return ResolvedRate(design.base_rate_paise, "base", None)

    # 4. BR-PR-01: explicit rejection — never default to zero
    raise RateNotFound(
        f"No rate found for design {design.design_no}. "
        "Add it to the rate card before it can be ordered.",
        {"design_no": design.design_no},
    )
