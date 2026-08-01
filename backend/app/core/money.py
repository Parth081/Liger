"""Money — integer paise, the only representation of money in Liger (rule R1).

Every rupee amount in the system is a whole number of paise held in a
``Money`` value. Floats are rejected at construction time so a float can
never silently leak into a financial calculation. Percentage and division
operations round with ROUND_HALF_UP per BR-PR-10.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

_PAISE_PER_RUPEE = 100


class Money:
    """An immutable amount of Indian currency, stored as integer paise."""

    __slots__ = ("_paise",)
    _paise: int

    def __init__(self, paise: int) -> None:
        if isinstance(paise, bool) or not isinstance(paise, int):
            raise TypeError(f"Money takes integer paise, got {type(paise).__name__}")
        object.__setattr__(self, "_paise", paise)

    def __setattr__(self, name: str, value: Any) -> None:  # pragma: no cover - guard
        raise AttributeError("Money is immutable")

    # ---------------- constructors ----------------
    @classmethod
    def from_rupees(cls, rupees: str | int | Decimal) -> Money:
        """Build from a rupee amount expressed as str/int/Decimal — never float."""
        if isinstance(rupees, float):
            raise TypeError("Money.from_rupees rejects float — pass str or Decimal (R1)")
        dec = Decimal(rupees)
        paise = (dec * _PAISE_PER_RUPEE).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return cls(int(paise))

    @classmethod
    def zero(cls) -> Money:
        return cls(0)

    # ---------------- accessors ----------------
    @property
    def paise(self) -> int:
        return self._paise

    @property
    def rupees(self) -> Decimal:
        return Decimal(self._paise) / _PAISE_PER_RUPEE

    # ---------------- arithmetic ----------------
    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(self._paise + other._paise)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(self._paise - other._paise)

    def __neg__(self) -> Money:
        return Money(-self._paise)

    def __mul__(self, factor: int | Decimal) -> Money:
        """Multiply by a scalar (quantity, sq.ft as Decimal). ROUND_HALF_UP (BR-PR-10)."""
        if isinstance(factor, bool) or isinstance(factor, float):
            raise TypeError("Money multiplication takes int or Decimal, not float (R1)")
        if isinstance(factor, int):
            return Money(self._paise * factor)
        if isinstance(factor, Decimal):
            result = (Decimal(self._paise) * factor).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            return Money(int(result))
        raise TypeError(f"Cannot multiply Money by {type(factor).__name__}")

    __rmul__ = __mul__

    def percent(self, pct: Decimal | int | str) -> Money:
        """pct% of this amount, ROUND_HALF_UP."""
        if isinstance(pct, float):
            raise TypeError("percent takes Decimal/int/str, not float (R1)")
        p = Decimal(pct)
        result = (Decimal(self._paise) * p / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return Money(int(result))

    def apportion(self, weights: list[int]) -> list[Money]:
        """Split this amount across weights with no paise lost (BR-PR-09).

        Largest-remainder method: each share floors, leftover paise go to the
        largest fractional remainders first. Sum of parts == self, always.
        """
        if not weights or any(w < 0 for w in weights):
            raise ValueError("apportion needs non-negative weights, at least one")
        total_weight = sum(weights)
        if total_weight == 0:
            raise ValueError("apportion weights sum to zero")
        shares: list[int] = []
        remainders: list[tuple[Decimal, int]] = []
        for idx, w in enumerate(weights):
            exact = Decimal(self._paise) * w / total_weight
            floor = int(exact.to_integral_value(rounding="ROUND_FLOOR"))
            shares.append(floor)
            remainders.append((exact - floor, idx))
        leftover = self._paise - sum(shares)
        # negative totals apportion symmetrically: distribute toward most-negative remainders
        step = 1 if leftover >= 0 else -1
        remainders.sort(key=lambda t: t[0], reverse=leftover >= 0)
        for i in range(abs(leftover)):
            shares[remainders[i % len(shares)][1]] += step
        return [Money(s) for s in shares]

    def round_to_rupee(self) -> tuple[Money, Money]:
        """Round to the nearest whole rupee (BR-PR-10).

        Returns (rounded_total, round_off_delta) where
        rounded_total = self + round_off_delta.
        """
        rupees = (Decimal(self._paise) / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        rounded = Money(int(rupees) * 100)
        return rounded, rounded - self

    # ---------------- comparison ----------------
    def __eq__(self, other: object) -> bool:
        return isinstance(other, Money) and self._paise == other._paise

    def __lt__(self, other: Money) -> bool:
        self._check(other)
        return self._paise < other._paise

    def __le__(self, other: Money) -> bool:
        self._check(other)
        return self._paise <= other._paise

    def __gt__(self, other: Money) -> bool:
        self._check(other)
        return self._paise > other._paise

    def __ge__(self, other: Money) -> bool:
        self._check(other)
        return self._paise >= other._paise

    def __hash__(self) -> int:
        return hash(("Money", self._paise))

    def __bool__(self) -> bool:
        return self._paise != 0

    # ---------------- display ----------------
    def format_inr(self) -> str:
        """Indian-style grouping: ₹1,23,45,678.90"""
        sign = "-" if self._paise < 0 else ""
        total = abs(self._paise)
        rupees, paise = divmod(total, 100)
        s = str(rupees)
        if len(s) > 3:
            head, tail = s[:-3], s[-3:]
            groups: list[str] = []
            while len(head) > 2:
                groups.insert(0, head[-2:])
                head = head[:-2]
            groups.insert(0, head)  # head is 1–2 digits here, never empty
            s = ",".join(groups + [tail])
        return f"{sign}₹{s}.{paise:02d}"

    def __repr__(self) -> str:
        return f"Money({self._paise})"

    def __str__(self) -> str:
        return self.format_inr()

    # ---------------- internal ----------------
    @staticmethod
    def _check(other: object) -> None:
        if not isinstance(other, Money):
            raise TypeError(f"Expected Money, got {type(other).__name__} (R1)")
