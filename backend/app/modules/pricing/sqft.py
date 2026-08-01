"""The sq.ft engine — BR-SQFT-01…11. ONE implementation for preview, cart,
quotation, order and invoice. Divergence between preview and bill is a P1
defect (BR-SQFT-11).

Canonical unit is INCHES (BR-SQFT-01). Callers convert feet at the boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.core import settings_registry
from app.core.exceptions import ValidationFailed

_TWO_DP = Decimal("0.01")
_SQIN_PER_SQFT = Decimal("144")


@dataclass(frozen=True)
class SqftResult:
    raw_sqft: Decimal          # actual area, 2 dp
    billable_sqft: Decimal     # per piece, after min rule + rounding step
    min_rule_applied: bool     # BR-SQFT-07: surfaced to the dealer, always
    line_area: Decimal         # billable_sqft × quantity
    notes: list[str]


def feet_inches_to_inches(feet: int | Decimal, inches: int | Decimal = 0) -> Decimal:
    """Boundary helper: 7 ft 6 in -> 90.00 inches. Decimal feet allowed (7.5 ft)."""
    return (Decimal(feet) * 12 + Decimal(inches)).quantize(_TWO_DP, rounding=ROUND_HALF_UP)


def calculate_sqft(
    db: Session,
    length_in: Decimal,
    breadth_in: Decimal,
    quantity: int,
) -> SqftResult:
    """BR-SQFT-02…09. Raises ValidationFailed on bad input — never coerces."""
    # BR-SQFT-08: strict positivity, field-level errors
    if length_in <= 0:
        raise ValidationFailed("Length must be greater than 0", {"field": "length"})
    if breadth_in <= 0:
        raise ValidationFailed("Breadth must be greater than 0", {"field": "breadth"})
    if quantity <= 0:
        raise ValidationFailed("Quantity must be at least 1", {"field": "quantity"})

    # BR-SQFT-09: typo guard
    max_dim = Decimal(settings_registry.get_int(db, "max_dimension_in"))
    if length_in > max_dim or breadth_in > max_dim:
        raise ValidationFailed(
            f"Dimension exceeds {max_dim} inches — please check the measurement",
            {"field": "length" if length_in > max_dim else "breadth"},
        )

    notes: list[str] = []

    # BR-SQFT-02: raw area, 2 dp HALF_UP
    raw_sqft = (length_in * breadth_in / _SQIN_PER_SQFT).quantize(_TWO_DP, rounding=ROUND_HALF_UP)

    # BR-SQFT-03/04 (DEC-01): minimum applies PER PIECE, before quantity
    min_sqft = settings_registry.get_decimal(db, "min_billable_sqft")
    billable = raw_sqft
    min_rule_applied = False
    if raw_sqft < min_sqft:
        billable = min_sqft
        min_rule_applied = True
        notes.append(f"Minimum {min_sqft} sq.ft applied (actual {raw_sqft} sq.ft)")

    # BR-SQFT-05 (DEC-02): round UP to the step; 0 disables. Exact multiples untouched.
    step = settings_registry.get_decimal(db, "sqft_rounding_step")
    if step > 0:
        steps = (billable / step).quantize(Decimal("1"), rounding=ROUND_CEILING)
        rounded = (steps * step).quantize(_TWO_DP, rounding=ROUND_HALF_UP)
        if rounded != billable:
            notes.append(f"Billable area rounded up to {rounded} sq.ft (step {step})")
        billable = rounded

    # BR-SQFT-06
    line_area = (billable * quantity).quantize(_TWO_DP, rounding=ROUND_HALF_UP)

    return SqftResult(
        raw_sqft=raw_sqft,
        billable_sqft=billable,
        min_rule_applied=min_rule_applied,
        line_area=line_area,
        notes=notes,
    )
