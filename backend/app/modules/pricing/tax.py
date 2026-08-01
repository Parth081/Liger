"""GST engine — BR-TAX-01…04.

Place of supply (BR-TAX-02): customer state == Liger's state -> CGST + SGST
at half each; different state -> IGST at the full rate. GST% comes from the
design (DEC-04) and is applied per line on the taxable value.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core import settings_registry
from app.core.money import Money

_GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z]\d[A-Z0-9][A-Z0-9]$")


@dataclass(frozen=True)
class TaxResult:
    cgst: Money
    sgst: Money
    igst: Money

    @property
    def total(self) -> Money:
        return self.cgst + self.sgst + self.igst


def is_intra_state(db: Session, customer_state: str | None) -> bool:
    """Unknown state defaults to intra-state (local walk-in trade)."""
    liger_state = settings_registry.get_str(db, "liger_state").strip().upper()
    if not customer_state:
        return True
    return customer_state.strip().upper() == liger_state


def compute_tax(taxable: Money, gst_pct: Decimal, intra_state: bool) -> TaxResult:
    """BR-TAX-01/02. Halves each round HALF_UP independently — standard GST
    practice; CGST+SGST may differ from IGST by 1 paise on odd amounts."""
    if intra_state:
        half = gst_pct / 2
        return TaxResult(cgst=taxable.percent(half), sgst=taxable.percent(half), igst=Money.zero())
    return TaxResult(cgst=Money.zero(), sgst=Money.zero(), igst=taxable.percent(gst_pct))


def validate_gstin(gstin: str | None) -> bool:
    """BR-TAX-04: format check; B2C (None/empty) is allowed."""
    if not gstin:
        return True
    return bool(_GSTIN_RE.match(gstin.strip().upper()))
