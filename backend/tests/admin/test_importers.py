"""P8 tests: importers dry-run/commit/re-run, opening balances (BR-LED-05),
reconciliation (P8-T2-09), settings audit (R8), and the enforcement switch."""
from datetime import date, timedelta

from app.core import settings_registry
from app.modules.admin import importers
from app.modules.credit import ledger
from app.modules.credit.models import Invoice, LedgerEntry
from app.modules.customers.models import Customer
from app.modules.identity.models import CustomerUser
from app.modules.orders.models import Order

TODAY = date.today()

CUSTOMERS_CSV = (
    "code,business_name,phone,state,city,credit_limit_rupees,credit_days,distributor_code\n"
    "DIST01,Gujarat Distributors,+919820000001,GJ,Surat,1000000,45,\n"
    "CUST01,Shah Furnishings,+919820000002,GJ,Surat,500000,30,DIST01\n"
    "CUST02,Mumbai Interiors,+919820000003,MH,Mumbai,300000,30,\n"
    "BAD01,,+91982,GJ,Surat,notanumber,,\n"
)

OPENING_CSV = (
    "customer_code,opening_balance_rupees\n"
    "CUST01,125000.50\n"
    "CUST02,0\n"
    "GHOST,999\n"
)


class TestCustomerImport:
    def test_dry_run_writes_nothing(self, db):
        report = importers.import_customers(db, CUSTOMERS_CSV, dry_run=True, actor_id=1)
        assert report.total == 4 and report.created == 3 and report.failed == 1
        assert any("BAD01" in e for e in report.errors)
        assert db.query(Customer).count() == 0

    def test_commit_creates_customers_and_logins(self, db):
        importers.import_customers(db, CUSTOMERS_CSV, dry_run=False, actor_id=1)
        assert db.query(Customer).count() == 3
        customer = db.query(Customer).filter(Customer.code == "CUST01").one()
        assert customer.credit_limit_paise == 50000000      # ₹5,00,000
        assert customer.credit_days == 30
        # a dealer login is created for the primary phone (BR-AC-09)
        assert db.query(CustomerUser).filter(
            CustomerUser.phone == "+919820000002").one().customer_id == customer.id

    def test_BR_AN_03_distributor_hierarchy_linked(self, db):
        importers.import_customers(db, CUSTOMERS_CSV, dry_run=False, actor_id=1)
        dealer = db.query(Customer).filter(Customer.code == "CUST01").one()
        distributor = db.query(Customer).filter(Customer.code == "DIST01").one()
        assert dealer.distributor_id == distributor.id

    def test_rerun_updates_not_duplicates(self, db):
        importers.import_customers(db, CUSTOMERS_CSV, dry_run=False, actor_id=1)
        report = importers.import_customers(db, CUSTOMERS_CSV, dry_run=False, actor_id=1)
        assert db.query(Customer).count() == 3
        assert report.updated == 3 and report.created == 0

    def test_missing_columns_rejected(self, db):
        import pytest

        from app.core.exceptions import ValidationFailed

        with pytest.raises(ValidationFailed):
            importers.import_customers(db, "a,b\n1,2\n", dry_run=True, actor_id=1)


class TestOpeningBalances:
    def _customers(self, db):
        importers.import_customers(db, CUSTOMERS_CSV, dry_run=False, actor_id=1)

    def test_BR_LED_05_posts_opening_entries(self, db):
        self._customers(db)
        report = importers.import_opening_balances(db, OPENING_CSV, dry_run=False,
                                                   actor_id=1)
        assert report.created == 2 and report.failed == 1     # GHOST not found
        customer = db.query(Customer).filter(Customer.code == "CUST01").one()
        assert ledger.current_balance(db, customer.id).paise == 12500050
        entry = db.query(LedgerEntry).filter(
            LedgerEntry.customer_id == customer.id).one()
        assert entry.entry_type == "opening"

    def test_never_double_posts(self, db):
        self._customers(db)
        importers.import_opening_balances(db, OPENING_CSV, dry_run=False, actor_id=1)
        second = importers.import_opening_balances(db, OPENING_CSV, dry_run=False,
                                                   actor_id=1)
        assert second.created == 0                            # re-run safe
        customer = db.query(Customer).filter(Customer.code == "CUST01").one()
        assert db.query(LedgerEntry).filter(
            LedgerEntry.customer_id == customer.id).count() == 1

    def test_dry_run_posts_nothing(self, db):
        self._customers(db)
        importers.import_opening_balances(db, OPENING_CSV, dry_run=True, actor_id=1)
        assert db.query(LedgerEntry).count() == 0


class TestInvoiceImport:
    def test_original_dates_preserved_so_ageing_is_real(self, db):
        importers.import_customers(db, CUSTOMERS_CSV, dry_run=False, actor_id=1)
        old = TODAY - timedelta(days=90)
        csv_content = (
            "customer_code,invoice_no,invoice_date,amount_rupees,amount_paid_rupees\n"
            f"CUST01,OLD/001,{old},50000,0\n"
            f"CUST01,OLD/002,{old},20000,20000\n"
        )
        report = importers.import_open_invoices(db, csv_content, dry_run=False, actor_id=1)
        assert report.created == 2
        unpaid = db.query(Invoice).filter(Invoice.invoice_no == "OLD/001").one()
        assert unpaid.invoice_date == old
        assert unpaid.status == "open"
        assert unpaid.due_date == old + timedelta(days=30)     # customer credit days
        paid = db.query(Invoice).filter(Invoice.invoice_no == "OLD/002").one()
        assert paid.status == "paid"

    def test_imported_invoice_ages_immediately(self, db):
        """The whole point: day one, the system already knows who is overdue."""
        from app.modules.credit.exposure import compute_position

        importers.import_customers(db, CUSTOMERS_CSV, dry_run=False, actor_id=1)
        old = TODAY - timedelta(days=90)
        importers.import_open_invoices(
            db,
            "customer_code,invoice_no,invoice_date,amount_rupees\n"
            f"CUST01,OLD/003,{old},50000\n",
            dry_run=False, actor_id=1,
        )
        customer = db.query(Customer).filter(Customer.code == "CUST01").one()
        position = compute_position(db, customer, TODAY)
        assert position.max_days_overdue == 60                 # 90 days - 30 credit days
        assert position.overdue_invoices[0].invoice_no == "OLD/003"

    def test_rerun_skips_existing(self, db):
        importers.import_customers(db, CUSTOMERS_CSV, dry_run=False, actor_id=1)
        csv_content = ("customer_code,invoice_no,invoice_date,amount_rupees\n"
                       f"CUST01,DUP/001,{TODAY},1000\n")
        importers.import_open_invoices(db, csv_content, dry_run=False, actor_id=1)
        second = importers.import_open_invoices(db, csv_content, dry_run=False, actor_id=1)
        assert second.created == 0 and db.query(Invoice).count() == 1


class TestOrderHistory:
    HISTORY = (
        "customer_code,order_no,order_date,design_no,design_name,category,"
        "length_ft,breadth_ft,quantity,amount_rupees\n"
        "CUST01,H/001,{d},LGR-100,Ivory Zebra,Zebra Blind,7,4,1,3500\n"
        "CUST01,H/001,{d},LGR-101,Grey Roller,Roller Blind,5,3,2,2200\n"
        "CUST02,H/002,{d},LGR-100,Ivory Zebra,Zebra Blind,6,4,1,3000\n"
    )

    def _content(self):
        return self.HISTORY.format(d=TODAY - timedelta(days=45))

    def test_groups_lines_into_orders(self, db):
        importers.import_customers(db, CUSTOMERS_CSV, dry_run=False, actor_id=1)
        report = importers.import_order_history(db, self._content(), dry_run=False,
                                                actor_id=1)
        assert report.created == 2                             # 2 orders, 3 lines
        order = db.query(Order).filter(Order.order_no == "H/001").one()
        assert order.grand_total_paise == 570000               # 3500 + 2200
        assert len(order.items) == 2
        assert order.status == "CLOSED"

    def test_history_amount_is_taken_as_billed(self, db):
        """We never re-price history — the old books are the truth."""
        importers.import_customers(db, CUSTOMERS_CSV, dry_run=False, actor_id=1)
        importers.import_order_history(db, self._content(), dry_run=False, actor_id=1)
        order = db.query(Order).filter(Order.order_no == "H/002").one()
        assert order.grand_total_paise == 300000

    def test_rerun_safe(self, db):
        importers.import_customers(db, CUSTOMERS_CSV, dry_run=False, actor_id=1)
        importers.import_order_history(db, self._content(), dry_run=False, actor_id=1)
        second = importers.import_order_history(db, self._content(), dry_run=False,
                                                actor_id=1)
        assert second.created == 0 and db.query(Order).count() == 2


class TestReconciliation:
    def test_P8_T2_09_clean_when_matching(self, db):
        importers.import_customers(db, CUSTOMERS_CSV, dry_run=False, actor_id=1)
        importers.import_opening_balances(db, OPENING_CSV, dry_run=False, actor_id=1)
        result = importers.reconcile(
            db, "customer_code,book_balance_rupees\nCUST01,125000.50\nCUST02,0\n")
        assert result["clean"] is True
        assert result["matched"] == 2 and result["variance_count"] == 0

    def test_variance_is_reported_per_customer(self, db):
        importers.import_customers(db, CUSTOMERS_CSV, dry_run=False, actor_id=1)
        importers.import_opening_balances(db, OPENING_CSV, dry_run=False, actor_id=1)
        result = importers.reconcile(
            db, "customer_code,book_balance_rupees\nCUST01,120000\n")
        assert result["clean"] is False
        variance = result["variances"][0]
        assert variance["customer_code"] == "CUST01"
        assert variance["variance_paise"] == 12500050 - 12000000
        assert result["total_variance_paise"] == 500050

    def test_missing_customer_flagged(self, db):
        result = importers.reconcile(
            db, "customer_code,book_balance_rupees\nNOPE,100\n")
        assert result["variances"][0]["status"] == "MISSING_IN_SYSTEM"

    def test_reconcile_writes_nothing(self, db):
        importers.import_customers(db, CUSTOMERS_CSV, dry_run=False, actor_id=1)
        before = db.query(LedgerEntry).count()
        importers.reconcile(db, "customer_code,book_balance_rupees\nCUST01,999\n")
        assert db.query(LedgerEntry).count() == before

    def test_summary_for_owner_signoff(self, db):
        importers.import_customers(db, CUSTOMERS_CSV, dry_run=False, actor_id=1)
        importers.import_opening_balances(db, OPENING_CSV, dry_run=False, actor_id=1)
        summary = importers.import_summary(db)
        assert summary["customers"] == 3
        assert summary["total_outstanding_paise"] == 12500050


class TestAdminAPI:
    def test_R11_only_super_admin_imports(self, client, db, as_staff):
        headers = {"Content-Type": "text/csv"}
        assert client.post("/api/v1/imports/customers", content=CUSTOMERS_CSV,
                           headers={**as_staff("admin"), **headers}).status_code == 403
        assert client.post("/api/v1/imports/customers?dry_run=true", content=CUSTOMERS_CSV,
                           headers={**as_staff("super_admin"), **headers}).status_code == 200

    def test_unknown_import_kind(self, client, db, as_staff):
        r = client.post("/api/v1/imports/nonsense", content="a\n1\n",
                        headers={**as_staff("super_admin"), "Content-Type": "text/csv"})
        assert r.status_code == 404

    def test_R8_settings_change_is_audited(self, client, db, as_staff):
        r = client.patch("/api/v1/settings/hard_block_days", json={"value": "60"},
                         headers=as_staff("super_admin"))
        assert r.status_code == 200
        assert settings_registry.get_int(db, "hard_block_days") == 60
        history = client.get("/api/v1/settings/history?key=hard_block_days",
                             headers=as_staff("super_admin")).json()
        assert history["items"][0]["old_value"] == "45"

    def test_BR_CR_40_enforcement_switch_is_a_setting(self, client, db, as_staff):
        """P9's go-live decision: shadow -> enforce, no deploy, fully audited."""
        assert settings_registry.get_str(db, "credit_enforcement_mode") == "shadow"
        r = client.patch("/api/v1/settings/credit_enforcement_mode",
                         json={"value": "enforce"}, headers=as_staff("super_admin"))
        assert r.status_code == 200
        assert settings_registry.get_str(db, "credit_enforcement_mode") == "enforce"
        # and it can be rolled back the same way (P9 rollback plan)
        client.patch("/api/v1/settings/credit_enforcement_mode",
                     json={"value": "shadow"}, headers=as_staff("super_admin"))
        assert settings_registry.get_str(db, "credit_enforcement_mode") == "shadow"

    def test_admin_cannot_change_settings(self, client, db, as_staff):
        r = client.patch("/api/v1/settings/hard_block_days", json={"value": "60"},
                         headers=as_staff("admin"))
        assert r.status_code == 403                            # BR-AC-01 only

    def test_audit_log_readable_by_super_admin_only(self, client, db, as_staff):
        assert client.get("/api/v1/audit-log",
                          headers=as_staff("admin")).status_code == 403
        assert client.get("/api/v1/audit-log",
                          headers=as_staff("super_admin")).status_code == 200
