"""P7 tests: aggregates match hand-computed fixtures, drill-down totals equal
their parent (BR-AN-04), distributor roll-up (BR-AN-03), rep scoping (BR-AC-04),
nudges (BR-AN-06)."""
from datetime import date, timedelta

from app.modules.analytics import service
from app.modules.analytics.service import Period
from app.modules.credit.models import Invoice
from app.modules.orders.models import Order, OrderItem

TODAY = date(2026, 8, 15)
MONTH = Period(date(2026, 8, 1), TODAY)

# Endpoints resolve "today" themselves, so API-level tests anchor to the real
# clock instead of the fixed fixture date.
REAL_TODAY = date.today()


def _order(db, customer, *, total_paise: int, days_ago: int = 0, status: str = "DELIVERED",
           design_no: str = "LGR-A1", category: str = "Zebra Blind", n: str | None = None,
           anchor: date | None = None):
    order = Order(
        order_no=n or f"A/{customer.id}/{days_ago}/{total_paise}",
        customer_id=customer.id, placed_by_type="user", placed_by_id=1,
        channel="staff", status=status,
        order_date=(anchor or TODAY) - timedelta(days=days_ago),
        subtotal_paise=total_paise, taxable_paise=total_paise,
        grand_total_paise=total_paise,
    )
    db.add(order)
    db.flush()
    db.add(OrderItem(
        order_id=order.id, design_no=design_no, design_name=f"Design {design_no}",
        category=category, length_in=84, breadth_in=48, quantity=1,
        raw_sqft=28, billable_sqft=28, line_area=28, rate_paise=10000,
        rate_source="base", taxable_paise=total_paise, gst_pct=0,
        line_total_paise=total_paise,
    ))
    db.commit()
    return order


class TestDashboard:
    def test_BR_AN_01_mtd_and_comparisons(self, db, customer_factory):
        c = customer_factory()
        _order(db, c, total_paise=100000, days_ago=5)      # this month
        _order(db, c, total_paise=200000, days_ago=10)     # this month
        _order(db, c, total_paise=500000, days_ago=40)     # last month
        result = service.dashboard(db, TODAY)
        assert result["sales"]["mtd_paise"] == 300000
        assert result["sales"]["mtd_orders"] == 2
        assert result["sales"]["last_month_paise"] == 500000
        assert result["sales"]["mom_change_pct"] == -40.0

    def test_cancelled_orders_excluded(self, db, customer_factory):
        c = customer_factory()
        _order(db, c, total_paise=100000, days_ago=1)
        _order(db, c, total_paise=999999, days_ago=1, status="CANCELLED", n="CANCELLED-1")
        assert service.dashboard(db, TODAY)["sales"]["mtd_paise"] == 100000

    def test_ageing_and_blocked_revenue(self, db, customer_factory):
        from app.core.money import Money
        from app.modules.credit import ledger

        c = customer_factory()
        c.status = "blocked"
        db.add(Invoice(invoice_no=f"INV-D-{c.id}", customer_id=c.id,
                       invoice_date=TODAY - timedelta(days=60),
                       due_date=TODAY - timedelta(days=30),
                       total_paise=250000, status="open"))
        ledger.post_entry(db, customer=c, entry_type="invoice", debit=Money(250000))
        db.commit()
        result = service.dashboard(db, TODAY)
        assert result["ageing"]["1-30"] == 250000
        assert result["money"]["blocked_revenue_paise"] == 250000
        assert result["customers"]["blocked"] == 1


class TestSlicing:
    def test_BR_AN_02_group_by_customer(self, db, customer_factory):
        a, b = customer_factory(), customer_factory()
        _order(db, a, total_paise=300000, days_ago=1)
        _order(db, b, total_paise=100000, days_ago=2)
        rows = service.sales_by(db, MONTH, ["customer"])
        assert [r["value_paise"] for r in rows] == [300000, 100000]   # ranked

    def test_group_by_month_and_state(self, db, customer_factory):
        gj = customer_factory(state="GJ")
        mh = customer_factory(state="MH")
        _order(db, gj, total_paise=100000, days_ago=1)
        _order(db, mh, total_paise=200000, days_ago=2)
        rows = service.sales_by(db, MONTH, ["state"])
        by_state = {r["state"]: r["value_paise"] for r in rows}
        assert by_state == {"GJ": 100000, "MH": 200000}

    def test_group_by_design_uses_line_totals(self, db, customer_factory):
        c = customer_factory()
        _order(db, c, total_paise=100000, days_ago=1, design_no="LGR-X")
        _order(db, c, total_paise=250000, days_ago=2, design_no="LGR-Y")
        rows = service.sales_by(db, MONTH, ["design"])
        assert {r["design"]: r["value_paise"] for r in rows} == {
            "LGR-X": 100000, "LGR-Y": 250000}

    def test_multi_dimension_grouping(self, db, customer_factory):
        c = customer_factory(state="GJ")
        _order(db, c, total_paise=100000, days_ago=1, category="Zebra Blind")
        _order(db, c, total_paise=200000, days_ago=2, category="Roller Blind")
        rows = service.sales_by(db, MONTH, ["state", "category"])
        assert len(rows) == 2
        assert all("state" in r and "category" in r for r in rows)

    def test_invalid_group_rejected(self, db):
        import pytest

        from app.core.exceptions import ValidationFailed

        with pytest.raises(ValidationFailed):
            service.sales_by(db, MONTH, ["nonsense"])


class TestDrilldown:
    def test_BR_AN_04_drilldown_total_equals_aggregate(self, db, customer_factory):
        c = customer_factory()
        _order(db, c, total_paise=100000, days_ago=1)
        _order(db, c, total_paise=200000, days_ago=3)
        aggregate = service.sales_by(db, MONTH, ["customer"])[0]
        detail = service.drilldown(db, MONTH, {"customer": c.business_name})
        assert sum(d["grand_total_paise"] for d in detail) == aggregate["value_paise"]
        assert len(detail) == aggregate["orders"]

    def test_drilldown_by_design(self, db, customer_factory):
        c = customer_factory()
        _order(db, c, total_paise=100000, days_ago=1, design_no="LGR-DD")
        _order(db, c, total_paise=500000, days_ago=1, design_no="LGR-OTHER")
        rows = service.drilldown(db, MONTH, {"design": "LGR-DD"})
        assert len(rows) == 1 and rows[0]["grand_total_paise"] == 100000


class TestDistributorRollup:
    def test_BR_AN_03_rollup_sums_sub_dealers(self, db, customer_factory):
        distributor = customer_factory(state="GJ")
        d1, d2 = customer_factory(state="GJ"), customer_factory(state="GJ")
        d1.distributor_id = distributor.id
        d2.distributor_id = distributor.id
        db.commit()
        _order(db, d1, total_paise=300000, days_ago=1)
        _order(db, d2, total_paise=200000, days_ago=2)
        _order(db, distributor, total_paise=100000, days_ago=3)   # own direct sales

        rows = service.distributor_rollup(db, MONTH)
        entry = next(r for r in rows if r["distributor"] == distributor.business_name)
        assert entry["dealers"] == 3          # 2 sub-dealers + itself
        assert entry["value_paise"] == 600000  # 3L + 2L + 1L


class TestScoping:
    def test_BR_AC_04_rep_sees_only_own_customers(self, db, customer_factory,
                                                  staff_factory):
        rep = staff_factory("sales_rep")
        mine = customer_factory()
        mine.sales_rep_id = rep.id
        theirs = customer_factory()
        db.commit()
        _order(db, mine, total_paise=100000, days_ago=1)
        _order(db, theirs, total_paise=900000, days_ago=1)

        scoped = service.dashboard(db, TODAY, rep_id=rep.id)
        assert scoped["sales"]["mtd_paise"] == 100000        # not 1,000,000
        rows = service.sales_by(db, MONTH, ["customer"], rep_id=rep.id)
        assert len(rows) == 1 and rows[0]["customer"] == mine.business_name

    def test_api_scoping(self, client, db, customer_factory, staff_factory, as_staff):
        rep = staff_factory("sales_rep")
        mine = customer_factory()
        mine.sales_rep_id = rep.id
        theirs = customer_factory()
        db.commit()
        _order(db, mine, total_paise=100000, anchor=REAL_TODAY)
        _order(db, theirs, total_paise=900000, anchor=REAL_TODAY, n="OTHER-1")
        body = client.get("/api/v1/analytics/dashboard",
                          headers=as_staff("sales_rep")).json()
        assert body["sales"]["mtd_paise"] == 100000

    def test_production_role_has_no_reports(self, client, db, as_staff):
        assert client.get("/api/v1/analytics/dashboard",
                          headers=as_staff("production")).status_code == 403


class TestCustomer360:
    def test_BR_AN_05_card_contents(self, db, customer_factory):
        c = customer_factory()
        for month in range(6):
            _order(db, c, total_paise=100000, days_ago=30 * month + 2,
                   design_no="LGR-FAV", n=f"F/{c.id}/{month}")
        card = service.customer_360(db, c, TODAY)
        assert card["orders_12m"] == 6
        assert card["avg_order_value_paise"] == 100000
        assert card["favourite_designs"][0]["design_no"] == "LGR-FAV"
        assert len(card["trend_12m"]) >= 5

    def test_BR_AN_06_dormant_nudge_uses_own_rhythm(self, db, customer_factory):
        c = customer_factory()
        for i in range(10):                              # monthly buyer...
            _order(db, c, total_paise=100000, days_ago=70 + 30 * i,
                   n=f"R/{c.id}/{i}")                    # ...silent 70 days
        card = service.customer_360(db, c, TODAY)
        assert any(n["type"] == "dormant" for n in card["nudges"])

    def test_BR_AN_06_limit_pressure_nudge(self, db, customer_factory):
        from app.core.money import Money
        from app.modules.credit import ledger

        c = customer_factory(credit_limit_rupees=1000)
        ledger.post_entry(db, customer=c, entry_type="invoice", debit=Money(95000))
        db.commit()
        card = service.customer_360(db, c, TODAY)
        assert any(n["type"] == "limit_pressure" for n in card["nudges"])

    def test_overdue_nudge(self, db, customer_factory):
        c = customer_factory()
        db.add(Invoice(invoice_no=f"INV-N-{c.id}", customer_id=c.id,
                       invoice_date=TODAY - timedelta(days=60),
                       due_date=TODAY - timedelta(days=25),
                       total_paise=100000, status="open"))
        db.commit()
        card = service.customer_360(db, c, TODAY)
        assert any(n["type"] == "overdue" for n in card["nudges"])


class TestReports:
    def test_min_rule_impact(self, db, customer_factory):
        """What the 11 sq.ft rule actually adds — the owner's own rule, measured."""
        c = customer_factory()
        order = _order(db, c, total_paise=110000, days_ago=1)
        item = db.query(OrderItem).filter(OrderItem.order_id == order.id).one()
        item.raw_sqft = 9
        item.billable_sqft = 11
        item.min_rule_applied = True
        item.quantity = 4
        db.commit()
        result = service.min_rule_impact(db, MONTH)
        assert result["lines_with_min_rule"] == 1
        assert result["extra_sqft_billed"] == 8.0        # (11-9) × 4

    def test_collections_by_method(self, db, customer_factory, staff_factory):
        from app.modules.payments import service as payments

        c = customer_factory()
        accounts = staff_factory("accounts")
        for method, amount in (("cash", 50000), ("bank_transfer", 150000)):
            payment = payments.record_offline(db, customer=c, amount_paise=amount,
                                              method=method, reference_no=None,
                                              slip_url=None, notes=None,
                                              actor_id=accounts.id)
            payments.confirm_offline(db, payment, actor_id=accounts.id)
        report = service.collections_report(db, Period(REAL_TODAY - timedelta(days=1),
                                                       REAL_TODAY + timedelta(days=1)))
        assert report["total_paise"] == 200000
        assert {r["method"] for r in report["by_method"]} == {"cash", "bank_transfer"}

    def test_outstanding_report_sorted(self, db, customer_factory):
        from app.core.money import Money
        from app.modules.credit import ledger

        small, big = customer_factory(), customer_factory()
        ledger.post_entry(db, customer=small, entry_type="invoice", debit=Money(10000))
        ledger.post_entry(db, customer=big, entry_type="invoice", debit=Money(90000))
        db.commit()
        rows = service.outstanding_report(db, TODAY)
        assert rows[0]["outstanding_paise"] == 90000

    def test_dashboard_api_shape(self, client, db, customer_factory, as_staff):
        c = customer_factory()
        _order(db, c, total_paise=100000, anchor=REAL_TODAY)
        body = client.get("/api/v1/analytics/dashboard", headers=as_staff("admin")).json()
        assert set(body) == {"as_of", "sales", "money", "ageing", "customers"}
        assert body["sales"]["mtd_paise"] == 100000
