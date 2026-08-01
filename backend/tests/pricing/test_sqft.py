"""BR-SQFT-01…09 — including ALL 8 worked examples from BUSINESS_RULES §1.

These tests are the safety net for every rupee the business bills.
"""
from decimal import Decimal

import pytest

from app.core import settings_registry
from app.core.exceptions import ValidationFailed
from app.modules.pricing.sqft import calculate_sqft, feet_inches_to_inches

D = Decimal


def ft(x: str) -> Decimal:
    """decimal feet -> inches"""
    return feet_inches_to_inches(D(x))


class TestFeetInchesConversion:
    def test_BR_SQFT_01_whole_feet(self):
        assert ft("7") == D("84.00")

    def test_BR_SQFT_01_feet_plus_inches(self):
        assert feet_inches_to_inches(7, 6) == D("90.00")

    def test_BR_SQFT_01_decimal_feet(self):
        assert ft("2.2") == D("26.40")


class TestWorkedExamples:
    """The exact table from BUSINESS_RULES §1 (defaults: min 11, step 0.25)."""

    @pytest.mark.parametrize(
        "l_ft,b_ft,qty,raw,billable,line_area",
        [
            ("7", "4", 1, "28.00", "28.00", "28.00"),      # above minimum
            ("3", "3", 1, "9.00", "11.00", "11.00"),       # minimum applied
            ("3", "3", 4, "9.00", "11.00", "44.00"),       # min per piece (DEC-01)
            ("5", "2.2", 1, "11.00", "11.00", "11.00"),    # exactly at boundary
            ("4", "2.6", 1, "10.40", "11.00", "11.00"),    # minimum applied
            ("7.5", "4.1", 2, "30.75", "30.75", "61.50"),  # already on 0.25 step
            ("6.4", "3.3", 1, "21.12", "21.25", "21.25"),  # rounded up to step
        ],
    )
    def test_BR_SQFT_worked_examples(self, db, l_ft, b_ft, qty, raw, billable, line_area):
        r = calculate_sqft(db, ft(l_ft), ft(b_ft), qty)
        assert r.raw_sqft == D(raw)
        assert r.billable_sqft == D(billable)
        assert r.line_area == D(line_area)

    def test_BR_SQFT_08_zero_length_rejected(self, db):
        with pytest.raises(ValidationFailed) as e:
            calculate_sqft(db, D("0"), ft("4"), 1)
        assert e.value.details["field"] == "length"


class TestMinRule:
    def test_BR_SQFT_04_min_applies_per_piece(self, db):
        r = calculate_sqft(db, ft("3"), ft("3"), 4)
        assert r.min_rule_applied is True
        assert r.line_area == D("44.00")  # 11 × 4, NOT max(36, 11)

    def test_BR_SQFT_07_both_numbers_in_notes(self, db):
        r = calculate_sqft(db, ft("3"), ft("3"), 1)
        assert any("11" in n and "9.00" in n for n in r.notes)

    def test_no_note_when_above_min(self, db):
        r = calculate_sqft(db, ft("7"), ft("4"), 1)
        assert r.min_rule_applied is False and r.notes == []

    def test_boundary_10_99_vs_11_01(self, db):
        just_under = calculate_sqft(db, D("110"), D("14.39"), 1)   # 10.99 sqft
        assert just_under.min_rule_applied is True
        just_over = calculate_sqft(db, D("110"), D("14.42"), 1)    # 11.02 sqft
        assert just_over.min_rule_applied is False

    def test_R8_min_comes_from_settings(self, db):
        settings_registry.set_value(db, "min_billable_sqft", "15.00", actor_id=None)
        r = calculate_sqft(db, ft("3"), ft("3"), 1)
        assert r.billable_sqft == D("15.00")


class TestRoundingStep:
    def test_BR_SQFT_05_rounds_up_not_half(self, db):
        # 21.12 -> 21.25 even though 21.00 is nearer (rounding is UP)
        r = calculate_sqft(db, ft("6.4"), ft("3.3"), 1)
        assert r.billable_sqft == D("21.25")

    def test_exact_multiple_untouched(self, db):
        r = calculate_sqft(db, ft("5"), ft("2.2"), 1)  # exactly 11.00
        assert r.billable_sqft == D("11.00")

    def test_R8_step_zero_disables(self, db):
        settings_registry.set_value(db, "sqft_rounding_step", "0", actor_id=None)
        r = calculate_sqft(db, ft("6.4"), ft("3.3"), 1)
        assert r.billable_sqft == D("21.12")


class TestGuards:
    def test_BR_SQFT_08_negative_breadth(self, db):
        with pytest.raises(ValidationFailed):
            calculate_sqft(db, ft("4"), D("-1"), 1)

    def test_BR_SQFT_08_zero_quantity(self, db):
        with pytest.raises(ValidationFailed) as e:
            calculate_sqft(db, ft("4"), ft("4"), 0)
        assert e.value.details["field"] == "quantity"

    def test_BR_SQFT_09_typo_guard(self, db):
        # 770 ft typed instead of 7.70 ft -> 9240 inches > 600 default
        with pytest.raises(ValidationFailed) as e:
            calculate_sqft(db, ft("770"), ft("4"), 1)
        assert "check the measurement" in e.value.message

    def test_BR_SQFT_09_guard_is_a_setting(self, db):
        settings_registry.set_value(db, "max_dimension_in", "10000", actor_id=None)
        r = calculate_sqft(db, ft("770"), ft("4"), 1)  # now allowed
        assert r.raw_sqft == D("3080.00")

    def test_quantity_999_allowed(self, db):
        r = calculate_sqft(db, ft("4"), ft("4"), 999)
        assert r.line_area == D("15984.00")
