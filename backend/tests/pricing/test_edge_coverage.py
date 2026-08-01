"""Closes the last branches of the pricing engine to 100% (P1 exit gate)."""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.core.exceptions import ValidationFailed
from app.modules.pricing.models import RateCard
from app.modules.pricing.rate_resolver import resolve_rate
from app.modules.pricing.service import price_cart, price_line
from app.modules.pricing.sqft import feet_inches_to_inches as fi

TODAY = date(2026, 8, 1)
D = Decimal


class TestResolverEdges:
    def test_BR_PR_01_published_card_missing_design_falls_to_base(self, db, design_factory):
        """A published card that lacks this design falls through to base rate."""
        d = design_factory("LGR-4001", rate_rupees="150")
        other = design_factory("LGR-4002", rate_rupees="100")
        card = RateCard(version=1, name="v1", status="published",
                        effective_from=TODAY - timedelta(days=1))
        db.add(card)
        db.flush()
        from app.modules.pricing.models import RateCardItem

        db.add(RateCardItem(rate_card_id=card.id, design_id=other.id, rate_paise=9999))
        db.commit()
        r = resolve_rate(db, d, TODAY, None)
        assert r.rate_paise == 15000 and r.rate_card_version is None


class TestServiceEdges:
    def test_BR_CAT_07_unknown_design_rejected(self, db):
        from app.core.exceptions import DesignNotFound
        from app.modules.pricing.service import get_design_ci

        with pytest.raises(DesignNotFound):
            get_design_ci(db, "GHOST-1")

    def test_negative_line_discount_rejected(self, db, design_factory):
        design_factory("LGR-4003", rate_rupees="100")
        with pytest.raises(ValidationFailed):
            price_line(db, design_no="LGR-4003", length_in=fi(D("7")), breadth_in=fi(D("4")),
                       quantity=1, on=TODAY, line_discount_paise=-1)

    def test_negative_order_discount_rejected(self, db, design_factory):
        design_factory("LGR-4004", rate_rupees="100")
        with pytest.raises(ValidationFailed):
            price_cart(db, items=[{"design_no": "LGR-4004", "length_in": fi(D("7")),
                                   "breadth_in": fi(D("4")), "quantity": 1}],
                       on=TODAY, order_discount_paise=-5)

    def test_negative_freight_rejected(self, db, design_factory):
        design_factory("LGR-4005", rate_rupees="100")
        with pytest.raises(ValidationFailed):
            price_cart(db, items=[{"design_no": "LGR-4005", "length_in": fi(D("7")),
                                   "breadth_in": fi(D("4")), "quantity": 1}],
                       on=TODAY, freight_paise=-1)
