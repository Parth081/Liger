"""BR-SCR-01…07 scoring with real history."""
from datetime import date, timedelta

from app.core import settings_registry
from app.core.money import Money
from app.modules.credit import ledger
from app.modules.credit.models import Invoice
from app.modules.credit.scoring import compute_score
from app.modules.orders.models import Order

TODAY = date(2026, 8, 1)


def _order(db, customer, total_paise: int, days_ago: int) -> Order:
    o = Order(
        order_no=f"T/{customer.id}/{days_ago}/{total_paise}",
        customer_id=customer.id, placed_by_type="user", placed_by_id=1,
        channel="staff", status="DELIVERED",
        order_date=TODAY - timedelta(days=days_ago),
        subtotal_paise=total_paise, taxable_paise=total_paise,
        grand_total_paise=total_paise,
    )
    db.add(o)
    db.commit()
    return o


class TestScoring:
    def test_established_customer_scores_and_suggests(self, db, customer_factory):
        c = customer_factory()
        # 12 months of monthly orders, ₹1L each
        for month in range(12):
            _order(db, c, 10_000_000, days_ago=30 * month + 10)
        # good cash history
        ledger.post_entry(db, customer=c, entry_type="payment", credit=Money(4_000_000),
                          meta={"method": "cash"})
        ledger.post_entry(db, customer=c, entry_type="payment", credit=Money(4_000_000),
                          meta={"method": "upi"})
        db.commit()
        row = compute_score(db, c, TODAY)
        assert row.band in ("A+", "A", "B", "C", "D")
        assert 0 <= row.score <= 100
        # BR-SCR-06: every factor carries a plain-language reason
        assert all("reason" in f for f in row.factors.values())
        # BR-SCR-04: suggestion derived from trailing 3-month average
        assert row.suggested_limit_paise is not None and row.suggested_limit_paise > 0

    def test_punctual_payer_scores_higher_than_late_payer(self, db, customer_factory):
        fast, slow = customer_factory(), customer_factory()
        for c in (fast, slow):
            for month in range(12):
                _order(db, c, 10_000_000, days_ago=30 * month + 10)
        # slow payer: settled invoices land far past due (updated_at ~ today)
        for i in range(3):
            db.add(Invoice(invoice_no=f"SLOW-{i}", customer_id=slow.id,
                           invoice_date=TODAY - timedelta(days=200),
                           due_date=TODAY - timedelta(days=170),
                           total_paise=100000, amount_paid_paise=100000, status="paid"))
        db.commit()
        assert compute_score(db, fast, TODAY).score >= compute_score(db, slow, TODAY).score

    def test_R8_weights_come_from_settings(self, db, customer_factory):
        c = customer_factory()
        for month in range(12):
            _order(db, c, 10_000_000, days_ago=30 * month + 10)
        before = compute_score(db, c, TODAY).score
        # crank volume weight to dominate — volume factor is high here
        settings_registry.set_value(db, "score_w_volume", "95", actor_id=None)
        settings_registry.set_value(db, "score_w_punctuality", "1", actor_id=None)
        after = compute_score(db, c, TODAY).score
        assert after != before or after == 100  # weights demonstrably applied

    def test_rerun_same_day_replaces_not_duplicates(self, db, customer_factory):
        from app.modules.credit.models import CustomerScore

        c = customer_factory()
        compute_score(db, c, TODAY)
        compute_score(db, c, TODAY)
        assert db.query(CustomerScore).filter_by(customer_id=c.id).count() == 1

    def test_ceiling_caps_suggestion(self, db, customer_factory):
        c = customer_factory()
        for month in range(12):
            _order(db, c, 50_000_000_000, days_ago=30 * month + 10)  # absurd volume
        settings_registry.set_value(db, "global_limit_ceiling_paise", "1000000", actor_id=None)
        row = compute_score(db, c, TODAY)
        assert row.suggested_limit_paise == 1000000
