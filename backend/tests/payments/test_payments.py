"""P4 tests: webhook idempotency (BR-PAY-03/04), the cash gate (BR-PAY-05),
FIFO allocation (BR-PAY-06), reversal (BR-PAY-09), credit notes (BR-TAX-06),
and the end-to-end loop: blocked -> pay -> unblocked -> order."""
import uuid
from datetime import date, timedelta

import pytest

from app.core import settings_registry
from app.core.exceptions import Conflict, ValidationFailed
from app.core.money import Money
from app.modules.credit import ledger
from app.modules.credit import service as credit_service
from app.modules.credit.models import Invoice, LedgerEntry
from app.modules.payments import service as payments
from app.modules.payments.gateway import FakeGateway, reset_gateway
from app.modules.payments.models import GatewayEvent, Payment

TODAY = date(2026, 8, 1)


@pytest.fixture(autouse=True)
def _fake_gateway():
    reset_gateway()
    yield
    reset_gateway()


def _invoice(db, customer, total_paise: int, due_days_ago: int, no=None) -> Invoice:
    inv = Invoice(invoice_no=no or f"INV-{customer.id}-{due_days_ago}-{total_paise}",
                  customer_id=customer.id,
                  invoice_date=TODAY - timedelta(days=due_days_ago + 30),
                  due_date=TODAY - timedelta(days=due_days_ago),
                  total_paise=total_paise, status="open")
    db.add(inv)
    db.commit()
    return inv


def _initiated_payment(db, customer, amount_paise: int) -> Payment:
    payment, _ = payments.initiate_online(
        db, customer=customer, amount_paise=amount_paise, method="upi",
        idempotency_key=str(uuid.uuid4()), actor_type="customer_user", actor_id=1,
    )
    return payment


def _webhook(client, db, payment: Payment, event_id: str):
    gw = FakeGateway()
    body, signature = gw.sign({
        "event_id": event_id, "event": "payment.captured",
        "gateway_order_id": payment.gateway_order_id,
        "gateway_payment_id": f"pay_{event_id}",
    })
    return client.post("/api/v1/webhooks/razorpay", content=body,
                       headers={"X-Razorpay-Signature": signature,
                                "Content-Type": "application/json"})


class TestWebhook:
    def test_BR_PAY_03_ledger_moves_only_via_webhook(self, client, db, customer_factory):
        c = customer_factory()
        payment = _initiated_payment(db, c, 500000)
        assert ledger.current_balance(db, c.id) == Money.zero()   # initiation posts nothing
        r = _webhook(client, db, payment, "evt_1")
        assert r.status_code == 200 and r.json()["status"] == "processed"
        # payment posted as credit -> balance goes negative (on account)
        assert ledger.current_balance(db, c.id) == Money(-500000)

    def test_BR_PAY_04_same_event_five_times_one_ledger_entry(self, client, db,
                                                              customer_factory):
        c = customer_factory()
        payment = _initiated_payment(db, c, 500000)
        for _ in range(5):
            r = _webhook(client, db, payment, "evt_dup")
            assert r.status_code == 200
        entries = db.query(LedgerEntry).filter(LedgerEntry.customer_id == c.id).all()
        assert len(entries) == 1
        assert db.query(GatewayEvent).filter_by(event_id="evt_dup").count() == 1

    def test_bad_signature_rejected_but_evidence_kept(self, client, db, customer_factory):
        c = customer_factory()
        payment = _initiated_payment(db, c, 500000)
        r = client.post("/api/v1/webhooks/razorpay",
                        json={"event_id": "evt_bad", "event": "payment.captured",
                              "gateway_order_id": payment.gateway_order_id},
                        headers={"X-Razorpay-Signature": "wrong"})
        assert r.status_code == 400
        assert ledger.current_balance(db, c.id) == Money.zero()
        stored = db.query(GatewayEvent).filter_by(event_id="evt_bad").one()
        assert stored.signature_valid is False                     # audit trail kept

    def test_non_capture_events_ignored(self, client, db):
        gw = FakeGateway()
        body, signature = gw.sign({"event_id": "evt_other", "event": "payment.failed"})
        r = client.post("/api/v1/webhooks/razorpay", content=body,
                        headers={"X-Razorpay-Signature": signature,
                                 "Content-Type": "application/json"})
        assert r.json()["status"] == "ignored"


class TestCashGate:
    def test_BR_PAY_05_unconfirmed_cash_frees_zero_credit(self, client, db,
                                                          customer_factory, as_staff):
        settings_registry.set_value(db, "credit_enforcement_mode", "enforce", actor_id=None)
        c = customer_factory()
        inv = _invoice(db, c, 100000, due_days_ago=20)
        credit_service.advance_ladder(db, TODAY)
        db.refresh(c)
        assert c.status == "blocked"

        rep = as_staff("sales_rep")
        r = client.post("/api/v1/payments/offline",
                        json={"customer_uid": str(c.uid), "amount_paise": 100000,
                              "method": "cash", "reference_no": "CASH-001"},
                        headers=rep)
        assert r.status_code == 201
        payment_uid = r.json()["uid"]

        # cash recorded but NOT confirmed: ledger untouched, still blocked
        assert ledger.current_balance(db, c.id) == Money.zero()
        db.refresh(c)
        assert c.status == "blocked"

        # sales rep CANNOT confirm (BR-AC-04)
        assert client.post(f"/api/v1/payments/{payment_uid}/confirm",
                           headers=rep).status_code == 403

        # accounts confirms -> ledger moves, invoice paid, auto-unblock (BR-CR-47)
        r = client.post(f"/api/v1/payments/{payment_uid}/confirm",
                        headers=as_staff("accounts"))
        assert r.status_code == 200
        db.refresh(c)
        db.refresh(inv)
        assert c.status == "active"
        assert inv.status == "paid"
        entry = db.query(LedgerEntry).filter_by(customer_id=c.id,
                                                entry_type="payment").one()
        assert entry.meta["method"] == "cash"       # feeds the cash-bonus ratio

    def test_reject_leaves_everything_untouched(self, client, db, customer_factory,
                                                as_staff):
        c = customer_factory()
        r = client.post("/api/v1/payments/offline",
                        json={"customer_uid": str(c.uid), "amount_paise": 50000,
                              "method": "cheque", "reference_no": "CHQ-9"},
                        headers=as_staff("accounts"))
        payment_uid = r.json()["uid"]
        r = client.post(f"/api/v1/payments/{payment_uid}/reject",
                        json={"reason": "cheque details illegible"},
                        headers=as_staff("accounts"))
        assert r.json()["status"] == "failed"
        assert ledger.current_balance(db, c.id) == Money.zero()

    def test_double_confirm_conflict(self, db, customer_factory, staff_factory):
        c = customer_factory()
        accounts = staff_factory("accounts")
        payment = payments.record_offline(db, customer=c, amount_paise=1000, method="cash",
                                          reference_no=None, slip_url=None, notes=None,
                                          actor_id=accounts.id)
        payments.confirm_offline(db, payment, actor_id=accounts.id)
        with pytest.raises(Conflict):
            payments.confirm_offline(db, payment, actor_id=accounts.id)


class TestAllocation:
    def test_BR_PAY_06_fifo_across_three_invoices(self, db, customer_factory,
                                                  staff_factory):
        c = customer_factory()
        accounts = staff_factory("accounts")
        old = _invoice(db, c, 100000, due_days_ago=40, no="INV-A")
        mid = _invoice(db, c, 100000, due_days_ago=20, no="INV-B")
        new = _invoice(db, c, 100000, due_days_ago=5, no="INV-C")

        payment = payments.record_offline(db, customer=c, amount_paise=250000,
                                          method="bank_transfer", reference_no="NEFT1",
                                          slip_url=None, notes=None, actor_id=accounts.id)
        payments.confirm_offline(db, payment, actor_id=accounts.id)

        db.refresh(old), db.refresh(mid), db.refresh(new)
        assert old.status == "paid" and mid.status == "paid"
        assert new.amount_paid_paise == 50000 and new.status == "open"   # partial

    def test_on_account_remainder(self, db, customer_factory, staff_factory):
        c = customer_factory()
        accounts = staff_factory("accounts")
        _invoice(db, c, 100000, due_days_ago=10)
        payment = payments.record_offline(db, customer=c, amount_paise=150000,
                                          method="cash", reference_no=None,
                                          slip_url=None, notes=None, actor_id=accounts.id)
        payments.confirm_offline(db, payment, actor_id=accounts.id)
        # ledger holds the full credit (the fixture invoice posted no debit),
        # allocation caps at the invoice outstanding — ₹500 stays on account
        assert ledger.current_balance(db, c.id) == Money(-150000)
        assert sum(a.amount_paise for a in payment.allocations) == 100000

    def test_manual_reallocation(self, db, customer_factory, staff_factory):
        c = customer_factory()
        accounts = staff_factory("accounts")
        a = _invoice(db, c, 100000, due_days_ago=40, no="INV-M1")
        b = _invoice(db, c, 100000, due_days_ago=20, no="INV-M2")
        payment = payments.record_offline(db, customer=c, amount_paise=100000,
                                          method="cash", reference_no=None,
                                          slip_url=None, notes=None, actor_id=accounts.id)
        payments.confirm_offline(db, payment, actor_id=accounts.id)   # FIFO -> INV-M1
        db.refresh(a)
        assert a.status == "paid"
        # admin re-allocates to INV-M2 instead
        payments.reallocate(db, payment, [{"invoice_uid": str(b.uid),
                                           "amount_paise": 100000}], actor_id=accounts.id)
        db.refresh(a), db.refresh(b)
        assert a.status == "open" and b.status == "paid"

    def test_over_allocation_rejected(self, db, customer_factory, staff_factory):
        c = customer_factory()
        accounts = staff_factory("accounts")
        inv = _invoice(db, c, 100000, due_days_ago=10)
        payment = payments.record_offline(db, customer=c, amount_paise=50000,
                                          method="cash", reference_no=None,
                                          slip_url=None, notes=None, actor_id=accounts.id)
        payments.confirm_offline(db, payment, actor_id=accounts.id)
        with pytest.raises(ValidationFailed):
            payments.reallocate(db, payment, [{"invoice_uid": str(inv.uid),
                                               "amount_paise": 60000}], actor_id=accounts.id)


class TestReversal:
    def test_BR_PAY_09_bounce_reopens_and_reages(self, db, customer_factory, staff_factory):
        settings_registry.set_value(db, "credit_enforcement_mode", "enforce", actor_id=None)
        c = customer_factory()
        accounts = staff_factory("accounts")
        inv = _invoice(db, c, 100000, due_days_ago=20)
        payment = payments.record_offline(db, customer=c, amount_paise=100000,
                                          method="cheque", reference_no="CHQ-1",
                                          slip_url=None, notes=None, actor_id=accounts.id)
        payments.confirm_offline(db, payment, actor_id=accounts.id)
        db.refresh(inv)
        assert inv.status == "paid"

        payments.reverse_payment(db, payment, reason="cheque bounced", actor_id=accounts.id)
        db.refresh(inv)
        assert inv.status == "open" and inv.outstanding_paise == 100000
        # ledger: credit then reversing debit -> net zero (append-only, BR-LED-01)
        assert ledger.current_balance(db, c.id) == Money.zero()
        entries = [e.entry_type for e in ledger.statement(db, c.id)]
        assert entries == ["payment", "reversal"]
        # the invoice re-ages on its ORIGINAL due date -> ladder resumes correctly
        from app.modules.credit.exposure import compute_position

        assert compute_position(db, c, TODAY).max_days_overdue == 20

    def test_only_confirmed_reversible(self, db, customer_factory, staff_factory):
        c = customer_factory()
        accounts = staff_factory("accounts")
        payment = payments.record_offline(db, customer=c, amount_paise=1000, method="cash",
                                          reference_no=None, slip_url=None, notes=None,
                                          actor_id=accounts.id)
        with pytest.raises(Conflict):
            payments.reverse_payment(db, payment, reason="oops", actor_id=accounts.id)


class TestInvoicing:
    _n = 0

    def _order(self, client, db, design_factory, customer_factory, as_staff):
        TestInvoicing._n += 1
        design_no = f"LGR-P{TestInvoicing._n}"
        design_factory(design_no, rate_rupees="100", gst_pct="12")
        c = customer_factory(state="GJ", credit_days=30)
        r = client.post("/api/v1/orders",
                        json={"customer_uid": str(c.uid),
                              "items": [{"design_no": design_no, "length_ft": "7",
                                         "breadth_ft": "4", "quantity": 1}]},
                        headers={**as_staff("admin"), "Idempotency-Key": str(uuid.uuid4())})
        return c, r.json()

    def test_invoice_freezes_and_posts_ledger(self, client, db, design_factory,
                                              customer_factory, as_staff):
        c, order = self._order(client, db, design_factory, customer_factory, as_staff)
        r = client.post("/api/v1/invoices", json={"order_uid": order["uid"]},
                        headers=as_staff("accounts"))
        assert r.status_code == 201
        body = r.json()
        assert body["invoice_no"].startswith("LGR/INV/2026-27/")
        assert body["total_paise"] == order["grand_total_paise"]
        # BR-CR-03: due = invoice date + credit days
        assert body["due_date"] == str(date.today() + timedelta(days=30))
        assert ledger.current_balance(db, c.id) == Money(order["grand_total_paise"])

    def test_double_invoice_conflict(self, client, db, design_factory, customer_factory,
                                     as_staff):
        _, order = self._order(client, db, design_factory, customer_factory, as_staff)
        client.post("/api/v1/invoices", json={"order_uid": order["uid"]},
                    headers=as_staff("accounts"))
        r = client.post("/api/v1/invoices", json={"order_uid": order["uid"]},
                        headers=as_staff("accounts"))
        assert r.status_code == 409

    def test_BR_TAX_06_credit_note_reduces_never_edits(self, client, db, design_factory,
                                                       customer_factory, as_staff):
        c, order = self._order(client, db, design_factory, customer_factory, as_staff)
        inv_body = client.post("/api/v1/invoices", json={"order_uid": order["uid"]},
                               headers=as_staff("accounts")).json()
        r = client.post("/api/v1/credit-notes",
                        json={"invoice_uid": inv_body["uid"], "amount_paise": 50000,
                              "reason": "damaged panel returned"},
                        headers=as_staff("accounts"))
        assert r.status_code == 201
        assert r.json()["credit_note_no"].startswith("LGR/CN/")
        inv = db.query(Invoice).filter(Invoice.invoice_no == inv_body["invoice_no"]).one()
        assert inv.total_paise == inv_body["total_paise"]      # original untouched
        assert inv.outstanding_paise == inv_body["total_paise"] - 50000
        assert ledger.current_balance(db, c.id) == Money(inv_body["total_paise"] - 50000)

    def test_gapless_numbering_sequence(self, client, db, design_factory,
                                        customer_factory, as_staff):
        headers = as_staff("accounts")
        numbers = []
        for _ in range(3):
            _, order = self._order(client, db, design_factory, customer_factory, as_staff)
            body = client.post("/api/v1/invoices", json={"order_uid": order["uid"]},
                               headers=headers).json()
            numbers.append(int(body["invoice_no"].rsplit("/", 1)[1]))
        assert numbers == [numbers[0], numbers[0] + 1, numbers[0] + 2]   # BR-TAX-05


class TestEndToEndLoop:
    def test_blocked_pays_online_unblocks_and_orders(self, client, db, design_factory,
                                                     customer_factory, dealer, as_dealer):
        """The whole point of the system, in one test."""
        settings_registry.set_value(db, "credit_enforcement_mode", "enforce", actor_id=None)
        design_factory("LGR-LOOP", rate_rupees="100", gst_pct="0")
        c = customer_factory(credit_limit_rupees=100000)
        dealer.customer_id = c.id
        db.commit()

        # old unpaid invoice -> ladder -> blocked
        inv = _invoice(db, c, 80000, due_days_ago=20)
        ledger.post_entry(db, customer=c, entry_type="invoice", debit=Money(80000),
                          ref_type="invoice", ref_id=inv.id)
        db.commit()
        credit_service.advance_ladder(db, TODAY)
        db.refresh(c)
        assert c.status == "blocked"

        # dealer tries to order -> 403 with pay info
        order_body = {"items": [{"design_no": "LGR-LOOP", "length_ft": "7",
                                 "breadth_ft": "4", "quantity": 1}]}
        r = client.post("/api/v1/orders", json=order_body,
                        headers={**as_dealer, "Idempotency-Key": str(uuid.uuid4())})
        assert r.status_code == 403
        assert r.json()["error"]["details"]["overdue_invoices"][0]["invoice_no"] == inv.invoice_no

        # dealer pays online -> webhook confirms -> auto-unblock
        r = client.post("/api/v1/payments/online/initiate",
                        json={"amount_paise": 80000, "method": "upi"},
                        headers={**as_dealer, "Idempotency-Key": str(uuid.uuid4())})
        assert r.status_code == 201
        payment = db.query(Payment).order_by(Payment.id.desc()).first()
        assert _webhook(client, db, payment, "evt_loop").json()["status"] == "processed"

        db.refresh(c)
        assert c.status == "active"                             # BR-CR-47, within seconds

        # and the order now goes through
        r = client.post("/api/v1/orders", json=order_body,
                        headers={**as_dealer, "Idempotency-Key": str(uuid.uuid4())})
        assert r.status_code == 201
        assert r.json()["status"] == "CONFIRMED"
