"""R1: Money is integer paise; floats are rejected; rounding is ROUND_HALF_UP.

100% branch coverage on core/money.py is a P0 exit-gate requirement.
"""
from decimal import Decimal

import pytest

from app.core.money import Money


class TestConstruction:
    def test_int_paise(self):
        assert Money(12345).paise == 12345

    def test_zero(self):
        assert Money.zero().paise == 0
        assert not Money.zero()
        assert Money(1)

    def test_R1_float_rejected(self):
        with pytest.raises(TypeError):
            Money(12.5)  # type: ignore[arg-type]

    def test_R1_bool_rejected(self):
        with pytest.raises(TypeError):
            Money(True)  # type: ignore[arg-type]

    def test_from_rupees_str(self):
        assert Money.from_rupees("1250.50").paise == 125050

    def test_from_rupees_decimal(self):
        assert Money.from_rupees(Decimal("0.01")).paise == 1

    def test_from_rupees_int(self):
        assert Money.from_rupees(99).paise == 9900

    def test_R1_from_rupees_float_rejected(self):
        with pytest.raises(TypeError):
            Money.from_rupees(12.5)  # type: ignore[arg-type]

    def test_from_rupees_half_up(self):
        # 0.005 rupees = 0.5 paise -> rounds up to 1 paise
        assert Money.from_rupees(Decimal("0.005")).paise == 1

    def test_immutable(self):
        m = Money(100)
        with pytest.raises(AttributeError):
            m._paise = 200  # type: ignore[misc]


class TestArithmetic:
    def test_add_sub_neg(self):
        assert (Money(100) + Money(50)).paise == 150
        assert (Money(100) - Money(150)).paise == -50
        assert (-Money(100)).paise == -100

    def test_R1_add_int_rejected(self):
        with pytest.raises(TypeError):
            Money(100) + 50  # type: ignore[operator]

    def test_mul_int(self):
        assert (Money(12500) * 4).paise == 50000
        assert (4 * Money(12500)).paise == 50000

    def test_mul_decimal_half_up(self):
        # 11 sqft * ₹1.25/sqft-ish: 125 paise * 11.005 = 1375.625 -> 1376
        assert (Money(125) * Decimal("11.005")).paise == 1376

    def test_R1_mul_float_rejected(self):
        with pytest.raises(TypeError):
            Money(100) * 1.5  # type: ignore[operator]

    def test_R1_mul_bool_rejected(self):
        with pytest.raises(TypeError):
            Money(100) * True  # type: ignore[operator]

    def test_mul_bad_type_rejected(self):
        with pytest.raises(TypeError):
            Money(100) * "2"  # type: ignore[operator]

    def test_percent(self):
        assert Money(10000).percent(Decimal("12")).paise == 1200
        assert Money(10000).percent("2.5").paise == 250
        assert Money(101).percent(50).paise == 51  # 50.5 -> HALF_UP -> 51

    def test_R1_percent_float_rejected(self):
        with pytest.raises(TypeError):
            Money(100).percent(1.5)  # type: ignore[arg-type]


class TestApportion:
    """BR-PR-09: order discount split pro-rata with zero paise lost."""

    def test_exact_split(self):
        parts = Money(300).apportion([1, 1, 1])
        assert [p.paise for p in parts] == [100, 100, 100]

    def test_remainder_distribution(self):
        parts = Money(100).apportion([1, 1, 1])
        assert sum(p.paise for p in parts) == 100
        assert sorted(p.paise for p in parts) == [33, 33, 34]

    def test_weighted(self):
        parts = Money(1000).apportion([3, 1])
        assert [p.paise for p in parts] == [750, 250]

    def test_sum_always_preserved(self):
        total = Money(99999)
        parts = total.apportion([7, 13, 29, 3, 1])
        assert sum(p.paise for p in parts) == total.paise

    def test_negative_total(self):
        parts = Money(-100).apportion([1, 1, 1])
        assert sum(p.paise for p in parts) == -100

    def test_empty_weights_rejected(self):
        with pytest.raises(ValueError):
            Money(100).apportion([])

    def test_negative_weight_rejected(self):
        with pytest.raises(ValueError):
            Money(100).apportion([1, -1])

    def test_zero_weights_rejected(self):
        with pytest.raises(ValueError):
            Money(100).apportion([0, 0])


class TestRounding:
    """BR-PR-10: final total rounds to the nearest rupee, difference recorded."""

    def test_round_up(self):
        total, off = Money(125050).round_to_rupee()
        assert total.paise == 125100 and off.paise == 50

    def test_round_down(self):
        total, off = Money(125049).round_to_rupee()
        assert total.paise == 125000 and off.paise == -49

    def test_already_round(self):
        total, off = Money(125000).round_to_rupee()
        assert total.paise == 125000 and off.paise == 0


class TestComparison:
    def test_eq_hash(self):
        assert Money(100) == Money(100)
        assert Money(100) != Money(101)
        assert Money(100) != 100
        assert hash(Money(100)) == hash(Money(100))

    def test_ordering(self):
        assert Money(1) < Money(2)
        assert Money(2) <= Money(2)
        assert Money(3) > Money(2)
        assert Money(3) >= Money(3)

    def test_R1_compare_int_rejected(self):
        with pytest.raises(TypeError):
            Money(100) < 200  # type: ignore[operator]
        with pytest.raises(TypeError):
            Money(100) <= 200  # type: ignore[operator]
        with pytest.raises(TypeError):
            Money(100) > 50  # type: ignore[operator]
        with pytest.raises(TypeError):
            Money(100) >= 50  # type: ignore[operator]


class TestFormat:
    """Indian grouping: ₹1,23,45,678.90"""

    def test_small(self):
        assert Money(12345).format_inr() == "₹123.45"

    def test_thousands(self):
        assert Money(1234500).format_inr() == "₹12,345.00"

    def test_lakhs(self):
        assert Money(12500000).format_inr() == "₹1,25,000.00"

    def test_crores(self):
        assert Money(1234567890).format_inr() == "₹1,23,45,678.90"

    def test_negative(self):
        assert Money(-125050).format_inr() == "-₹1,250.50"

    def test_str_repr(self):
        assert str(Money(100)) == "₹1.00"
        assert repr(Money(100)) == "Money(100)"

    def test_rupees_property(self):
        assert Money(125050).rupees == Decimal("1250.5")
