"""BR-PR-01…04 rate cascade (DEC-03: no tiers) + BR-TAX-01…04 GST."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.core.exceptions import RateNotFound
from app.core.money import Money
from app.modules.pricing.models import CustomerSpecialRate, RateCard, RateCardItem
from app.modules.pricing.rate_resolver import resolve_rate
from app.modules.pricing.tax import compute_tax, is_intra_state, validate_gstin

TODAY = date(2026, 8, 1)


def _publish_card(db, design, rate_paise: int, version: int = 1) -> RateCard:
    card = RateCard(version=version, name=f"v{version}", status="published",
                    effective_from=TODAY - timedelta(days=30))
    db.add(card)
    db.flush()
    db.add(RateCardItem(rate_card_id=card.id, design_id=design.id, rate_paise=rate_paise))
    db.commit()
    return card


class TestRateCascade:
    def test_BR_PR_01_design_base_rate_fallback(self, db, design_factory):
        d = design_factory("LGR-1001", rate_rupees="150")
        r = resolve_rate(db, d, TODAY, customer_id=None)
        assert r.rate_paise == 15000 and r.rate_source == "base"

    def test_BR_PR_01_rate_card_beats_base(self, db, design_factory):
        d = design_factory("LGR-1002", rate_rupees="150")
        _publish_card(db, d, 13500)
        r = resolve_rate(db, d, TODAY, customer_id=None)
        assert r.rate_paise == 13500 and r.rate_source == "base"
        assert r.rate_card_version == 1

    def test_BR_PR_01_special_beats_everything(self, db, design_factory, customer_factory):
        d = design_factory("LGR-1003", rate_rupees="150")
        c = customer_factory()
        _publish_card(db, d, 13500)
        db.add(CustomerSpecialRate(customer_id=c.id, design_id=d.id, rate_paise=12000,
                                   valid_from=TODAY - timedelta(days=1),
                                   valid_to=TODAY + timedelta(days=30)))
        db.commit()
        r = resolve_rate(db, d, TODAY, customer_id=c.id)
        assert r.rate_paise == 12000 and r.rate_source == "special"  # BR-PR-02

    def test_BR_PR_04_expired_special_ignored(self, db, design_factory, customer_factory):
        d = design_factory("LGR-1004", rate_rupees="150")
        c = customer_factory()
        db.add(CustomerSpecialRate(customer_id=c.id, design_id=d.id, rate_paise=12000,
                                   valid_from=TODAY - timedelta(days=60),
                                   valid_to=TODAY - timedelta(days=1)))
        db.commit()
        r = resolve_rate(db, d, TODAY, customer_id=c.id)
        assert r.rate_source == "base" and r.rate_paise == 15000

    def test_BR_PR_01_no_rate_is_explicit_rejection(self, db, design_factory):
        d = design_factory("LGR-1005", rate_rupees="0")  # no base rate, no card
        with pytest.raises(RateNotFound) as e:
            resolve_rate(db, d, TODAY, customer_id=None)
        assert "LGR-1005" in e.value.message  # never a silent zero

    def test_BR_PR_03_new_version_wins_from_effective_date(self, db, design_factory):
        d = design_factory("LGR-1006", rate_rupees="150")
        _publish_card(db, d, 13500, version=1)
        # v2 published later at a higher rate
        card2 = RateCard(version=2, name="v2", status="published", effective_from=TODAY)
        db.add(card2)
        db.flush()
        db.add(RateCardItem(rate_card_id=card2.id, design_id=d.id, rate_paise=14000))
        db.commit()
        assert resolve_rate(db, d, TODAY, None).rate_paise == 14000
        # historical date still resolves against v1 (R9 for re-pricing old docs)
        assert resolve_rate(db, d, TODAY - timedelta(days=10), None).rate_paise == 13500


class TestTax:
    def test_BR_TAX_02_intra_state_split(self, db):
        t = compute_tax(Money(594000), Decimal("12"), intra_state=True)
        assert t.cgst.paise == 35640 and t.sgst.paise == 35640 and t.igst.paise == 0
        assert t.total.paise == 71280

    def test_BR_TAX_02_inter_state_igst(self, db):
        t = compute_tax(Money(594000), Decimal("12"), intra_state=False)
        assert t.igst.paise == 71280 and t.cgst.paise == 0 and t.sgst.paise == 0

    def test_place_of_supply(self, db):
        assert is_intra_state(db, "GJ") is True       # liger_state=GJ seeded
        assert is_intra_state(db, "gj ") is True
        assert is_intra_state(db, "MH") is False
        assert is_intra_state(db, None) is True       # walk-in default

    def test_BR_TAX_04_gstin_format(self):
        assert validate_gstin("24AAACL1234A1Z5") is True
        assert validate_gstin(None) is True            # B2C allowed
        assert validate_gstin("") is True
        assert validate_gstin("BADGSTIN") is False
        assert validate_gstin("24aaacl1234a1z5") is True  # case-insensitive
