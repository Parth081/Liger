"""BR-PR-05…11 — full cart pricing: making charges, discounts, apportionment,
rounding, and the preview==bill invariant."""
from datetime import date
from decimal import Decimal

import pytest

from app.core.exceptions import ValidationFailed
from app.modules.pricing.models import MakingCharge
from app.modules.pricing.service import price_cart, price_line
from app.modules.pricing.sqft import feet_inches_to_inches as fi

TODAY = date(2026, 8, 1)
D = Decimal


def _item(design_no, l_ft, b_ft, qty, disc=0):
    return {"design_no": design_no, "length_in": fi(D(l_ft)), "breadth_in": fi(D(b_ft)),
            "quantity": qty, "line_discount_paise": disc}


class TestPriceLine:
    def test_BR_PR_05_goods_amount(self, db, design_factory):
        design_factory("LGR-2001", rate_rupees="125")
        line, _ = price_line(db, design_no="LGR-2001", length_in=fi(D("7")), breadth_in=fi(D("4")),
                             quantity=1, on=TODAY)
        # 28 sqft × ₹125 = ₹3500
        assert line.goods_amount.paise == 350000

    def test_min_rule_line_amount(self, db, design_factory):
        design_factory("LGR-2002", rate_rupees="125")
        line, _ = price_line(db, design_no="LGR-2002", length_in=fi(D("3")), breadth_in=fi(D("3")),
                             quantity=4, on=TODAY)
        # 11 × 4 × 125 = ₹5500, and both figures surfaced (BR-SQFT-07)
        assert line.goods_amount.paise == 550000
        assert line.min_rule_applied is True and line.notes

    def test_BR_CAT_01_case_insensitive_lookup(self, db, design_factory):
        design_factory("LGR-2003")
        line, _ = price_line(db, design_no="lgr-2003", length_in=fi(D("7")), breadth_in=fi(D("4")),
                             quantity=1, on=TODAY)
        assert line.design_no == "LGR-2003"

    def test_BR_CAT_04_discontinued_rejected(self, db, design_factory):
        design_factory("LGR-2004", status="discontinued")
        with pytest.raises(ValidationFailed) as e:
            price_line(db, design_no="LGR-2004", length_in=fi(D("7")), breadth_in=fi(D("4")),
                       quantity=1, on=TODAY)
        assert "discontinued" in e.value.message

    def test_BR_PR_06_making_charge_per_sqft(self, db, design_factory):
        design_factory("LGR-2005", rate_rupees="125", category_code="CURTAIN")
        db.add(MakingCharge(product_type="curtain", mode="per_sqft", amount_paise=1000))
        db.commit()
        line, _ = price_line(db, design_no="LGR-2005", length_in=fi(D("7")), breadth_in=fi(D("4")),
                             quantity=1, on=TODAY)
        assert line.making_charge.paise == 28000  # 28 sqft × ₹10

    def test_BR_PR_06_making_charge_per_piece(self, db, design_factory):
        design_factory("LGR-2006", rate_rupees="125", category_code="ROMAN")
        db.add(MakingCharge(product_type="roman", mode="per_piece", amount_paise=50000))
        db.commit()
        line, _ = price_line(db, design_no="LGR-2006", length_in=fi(D("3")), breadth_in=fi(D("3")),
                             quantity=4, on=TODAY)
        assert line.making_charge.paise == 200000  # ₹500 × 4 pieces

    def test_BR_PR_07_rep_discount_above_cap_needs_approval(self, db, design_factory):
        design_factory("LGR-2007", rate_rupees="100")
        # goods = 28 sqft × ₹100 = ₹2800; cap 5% = ₹140; ask ₹300
        _, reasons = price_line(db, design_no="LGR-2007", length_in=fi(D("7")), breadth_in=fi(D("4")),
                                quantity=1, on=TODAY, line_discount_paise=30000, is_sales_rep=True)
        assert reasons and "cap" in reasons[0]

    def test_rep_discount_within_cap_ok(self, db, design_factory):
        design_factory("LGR-2008", rate_rupees="100")
        _, reasons = price_line(db, design_no="LGR-2008", length_in=fi(D("7")), breadth_in=fi(D("4")),
                                quantity=1, on=TODAY, line_discount_paise=10000, is_sales_rep=True)
        assert reasons == []

    def test_discount_exceeding_line_rejected(self, db, design_factory):
        design_factory("LGR-2009", rate_rupees="100")
        with pytest.raises(ValidationFailed):
            price_line(db, design_no="LGR-2009", length_in=fi(D("7")), breadth_in=fi(D("4")),
                       quantity=1, on=TODAY, line_discount_paise=999999999)


class TestPriceCart:
    def test_BR_PR_09_order_discount_apportioned_before_tax(self, db, design_factory):
        design_factory("LGR-3001", rate_rupees="100", gst_pct="12")
        design_factory("LGR-3002", rate_rupees="100", gst_pct="18")
        cart = price_cart(
            db,
            items=[_item("LGR-3001", "7", "4", 1),      # taxable 280000
                   _item("LGR-3002", "7", "4", 3)],     # taxable 840000
            on=TODAY, customer_state="GJ",
            order_discount_paise=100000,                 # ₹1000 split 1:3
        )
        assert [s.paise for s in cart.line_discount_shares] == [25000, 75000]
        # tax computed on post-discount taxable: (280000-25000)×12%, (840000-75000)×18%
        assert cart.line_taxes[0].total.paise == 30600
        assert cart.line_taxes[1].total.paise == 137700

    def test_BR_PR_10_paise_reconcile_exactly(self, db, design_factory):
        """3-line odd split: apportioned paise must sum to the discount exactly."""
        design_factory("LGR-3003", rate_rupees="99.99")
        cart = price_cart(
            db,
            items=[_item("LGR-3003", "7", "4", 1),
                   _item("LGR-3003", "5", "3", 1),
                   _item("LGR-3003", "3", "3", 2)],
            on=TODAY, customer_state="GJ", order_discount_paise=99999,
        )
        assert sum(s.paise for s in cart.line_discount_shares) == 99999

    def test_BR_PR_10_round_off_to_rupee(self, db, design_factory):
        design_factory("LGR-3004", rate_rupees="99.99", gst_pct="12")
        cart = price_cart(db, items=[_item("LGR-3004", "7", "4", 1)], on=TODAY, customer_state="GJ")
        assert cart.grand_total.paise % 100 == 0
        assert cart.grand_total.paise == (cart.taxable_total + cart.cgst + cart.sgst
                                          + cart.igst).paise + cart.round_off.paise

    def test_BR_TAX_02_inter_state_cart_uses_igst(self, db, design_factory):
        design_factory("LGR-3005", rate_rupees="100", gst_pct="12")
        cart = price_cart(db, items=[_item("LGR-3005", "7", "4", 1)], on=TODAY, customer_state="MH")
        assert cart.igst.paise == 33600 and cart.cgst.paise == 0

    def test_freight_and_packing_added(self, db, design_factory):
        design_factory("LGR-3006", rate_rupees="100", gst_pct="0")
        cart = price_cart(db, items=[_item("LGR-3006", "7", "4", 1)], on=TODAY,
                          customer_state="GJ", freight_paise=50000, packing_paise=10000)
        assert cart.grand_total.paise == 280000 + 50000 + 10000

    def test_empty_cart_rejected(self, db):
        with pytest.raises(ValidationFailed):
            price_cart(db, items=[], on=TODAY)

    def test_discount_over_subtotal_rejected(self, db, design_factory):
        design_factory("LGR-3007", rate_rupees="100")
        with pytest.raises(ValidationFailed):
            price_cart(db, items=[_item("LGR-3007", "7", "4", 1)], on=TODAY,
                       order_discount_paise=999999999)

    def test_BR_PR_11_preview_equals_repeat_pricing(self, db, design_factory):
        """Same inputs price identically every time — preview == bill."""
        design_factory("LGR-3008", rate_rupees="123.45", gst_pct="18")
        items = [_item("LGR-3008", "6.4", "3.3", 3), _item("LGR-3008", "3", "3", 2)]
        a = price_cart(db, items=items, on=TODAY, customer_state="GJ", order_discount_paise=7777)
        b = price_cart(db, items=items, on=TODAY, customer_state="GJ", order_discount_paise=7777)
        assert a.grand_total == b.grand_total
        assert [ln.taxable.paise for ln in a.lines] == [ln.taxable.paise for ln in b.lines]
