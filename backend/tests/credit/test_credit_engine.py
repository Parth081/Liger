"""P3 credit engine tests — the highest-stakes tests in the project.

Covers: ledger append-only (R2), exposure incl. uninvoiced orders (BR-CR-02),
gate rule order (BR-CR-10…16), shadow mode (BR-CR-40), ladder idempotency
(BR-CR-49), auto-unblock (BR-CR-47), overrides (BR-CR-50), colours, scoring.
"""
from datetime import date, timedelta

import pytest
import sqlalchemy as sa

from app.core import settings_registry
from app.core.exceptions import ValidationFailed
from app.core.money import Money
from app.modules.credit import gate, ledger
from app.modules.credit import service as credit_service
from app.modules.credit.exposure import colour_state, compute_position
from app.modules.credit.models import (
    CreditEvent,
    EscalationState,
    Invoice,
)

TODAY = date(2026, 8, 1)


def _enforce(db):
    settings_registry.set_value(db, "credit_enforcement_mode", "enforce", actor_id=None)


def _invoice(db, customer, total_paise: int, due_days_ago: int, paid_paise: int = 0,
             no: str | None = None) -> Invoice:
    inv = Invoice(
        invoice_no=no or f"INV-{customer.id}-{due_days_ago}-{total_paise}",
        customer_id=customer.id,
        invoice_date=TODAY - timedelta(days=due_days_ago + 30),
        due_date=TODAY - timedelta(days=due_days_ago),
        total_paise=total_paise,
        amount_paid_paise=paid_paise,
        status="open",
    )
    db.add(inv)
    db.commit()
    return inv


class TestLedger:
    def test_BR_LED_01_append_only_enforced_by_db(self, db, customer_factory):
        c = customer_factory()
        entry = ledger.post_entry(db, customer=c, entry_type="invoice",
                                  debit=Money(100000), narration="test")
        db.commit()
        with pytest.raises(sa.exc.IntegrityError):
            db.execute(sa.text("UPDATE ledger_entries SET debit_paise = 1 WHERE id = :i"),
                       {"i": entry.id})
        db.rollback()
        with pytest.raises(sa.exc.IntegrityError):
            db.execute(sa.text("DELETE FROM ledger_entries WHERE id = :i"), {"i": entry.id})
        db.rollback()

    def test_BR_LED_02_balance_chain(self, db, customer_factory):
        c = customer_factory()
        ledger.post_entry(db, customer=c, entry_type="invoice", debit=Money(500000))
        ledger.post_entry(db, customer=c, entry_type="payment", credit=Money(200000))
        ledger.post_entry(db, customer=c, entry_type="invoice", debit=Money(100000))
        db.commit()
        entries = ledger.statement(db, c.id)
        assert [e.balance_after_paise for e in entries] == [500000, 300000, 400000]
        assert ledger.current_balance(db, c.id) == Money(400000)

    def test_BR_LED_04_reconciliation(self, db, customer_factory):
        c = customer_factory()
        ledger.post_entry(db, customer=c, entry_type="invoice", debit=Money(750000))
        ledger.post_entry(db, customer=c, entry_type="payment", credit=Money(250000))
        db.commit()
        assert ledger.current_balance(db, c.id) == ledger.derived_balance(db, c.id)

    def test_BR_LED_05_opening_balance_once(self, db, customer_factory):
        c = customer_factory()
        ledger.post_opening_balance(db, c, Money(1200000), actor_id=None)
        db.commit()
        with pytest.raises(ValidationFailed):
            ledger.post_opening_balance(db, c, Money(1), actor_id=None)

    def test_invalid_postings_rejected(self, db, customer_factory):
        c = customer_factory()
        with pytest.raises(ValidationFailed):
            ledger.post_entry(db, customer=c, entry_type="invoice")  # neither side
        with pytest.raises(ValidationFailed):
            ledger.post_entry(db, customer=c, entry_type="invoice",
                              debit=Money(1), credit=Money(1))       # both sides


class TestExposure:
    def test_BR_CR_01_outstanding_from_ledger(self, db, customer_factory):
        c = customer_factory()
        ledger.post_opening_balance(db, c, Money(300000), actor_id=None)
        db.commit()
        p = compute_position(db, c, TODAY)
        assert p.outstanding == Money(300000)

    def test_BR_CR_02_confirmed_uninvoiced_orders_count(self, db, customer_factory,
                                                        design_factory, dealer, client,
                                                        as_dealer):
        import uuid

        design_factory("LGR-C1", rate_rupees="100", gst_pct="0")
        c = customer_factory(credit_limit_rupees=100000)
        dealer.customer_id = c.id
        db.commit()
        client.post("/api/v1/orders",
                    json={"items": [{"design_no": "LGR-C1", "length_ft": "7",
                                     "breadth_ft": "4", "quantity": 1}]},
                    headers={**as_dealer, "Idempotency-Key": str(uuid.uuid4())})
        p = compute_position(db, c, TODAY)
        assert p.uninvoiced_orders == Money(280000)   # ten same-day orders can't beat the check
        assert p.exposure == Money(280000)

    def test_BR_CR_04_ageing_buckets(self, db, customer_factory):
        c = customer_factory()
        _invoice(db, c, 100000, due_days_ago=-5)     # current
        _invoice(db, c, 200000, due_days_ago=10)     # 1-30
        _invoice(db, c, 300000, due_days_ago=45)     # 31-60
        _invoice(db, c, 400000, due_days_ago=75)     # 61-90
        _invoice(db, c, 500000, due_days_ago=120)    # 90+
        p = compute_position(db, c, TODAY)
        buckets = {b.label: b.amount.paise for b in p.buckets}
        assert buckets == {"current": 100000, "1-30": 200000, "31-60": 300000,
                           "61-90": 400000, "90+": 500000}
        assert p.max_days_overdue == 120

    def test_partially_paid_invoice_ages_outstanding_only(self, db, customer_factory):
        c = customer_factory()
        _invoice(db, c, 100000, due_days_ago=10, paid_paise=60000)
        p = compute_position(db, c, TODAY)
        assert p.overdue_invoices[0].amount_paise == 40000

    def test_BR_CR_05_override_extends_limit(self, db, customer_factory, staff_factory):
        c = customer_factory(credit_limit_rupees=100000)
        admin = staff_factory("admin")
        credit_service.grant_override(db, c, extra_limit_paise=5000000,
                                      valid_until=TODAY + timedelta(days=7),
                                      reason="Diwali season", actor_id=admin.id)
        p = compute_position(db, c, TODAY)
        assert p.effective_limit == Money(100000 * 100 + 5000000)

    def test_BR_CR_06_cash_bonus_needs_ratio(self, db, customer_factory):
        c = customer_factory(credit_limit_rupees=100000)   # bonus pct default 10
        # no payment history -> ratio 0 -> no bonus
        assert compute_position(db, c, TODAY).cash_bonus == Money.zero()
        # 40% of payments in cash -> above 0.30 threshold -> bonus applies
        ledger.post_entry(db, customer=c, entry_type="invoice", debit=Money(1000000))
        ledger.post_entry(db, customer=c, entry_type="payment", credit=Money(400000),
                          meta={"method": "cash"})
        ledger.post_entry(db, customer=c, entry_type="payment", credit=Money(600000),
                          meta={"method": "upi"})
        db.commit()
        p = compute_position(db, c, TODAY)
        assert p.cash_bonus == Money(1000000)   # 10% of ₹1L limit


class TestGateOrder:
    """BR-CR-10…16 — first match wins, in exact order. Enforcement on."""

    def _customer(self, db, customer_factory, limit_rupees=100000):
        _enforce(db)
        return customer_factory(credit_limit_rupees=limit_rupees)

    def test_BR_CR_10_blocked_customer(self, db, customer_factory):
        c = self._customer(db, customer_factory)
        c.status = "blocked"
        db.commit()
        d = gate.evaluate(db, c, Money(100), on=TODAY)
        assert d.decision == "BLOCK"

    def test_BR_CR_11_hard_overdue_blocks_by_age_not_amount(self, db, customer_factory):
        c = self._customer(db, customer_factory)
        _invoice(db, c, 5000, due_days_ago=50)     # tiny amount, 50 days old
        d = gate.evaluate(db, c, Money(100), on=TODAY)
        assert d.decision == "BLOCK"
        assert d.reasons == ["OVERDUE_BEYOND_45_DAYS"]
        assert d.suggested_payment_paise == 5000

    def test_BR_CR_12_prepaid_bypasses_limit(self, db, customer_factory):
        c = self._customer(db, customer_factory, limit_rupees=1)
        d = gate.evaluate(db, c, Money(99999999), is_prepaid=True, on=TODAY)
        assert d.decision == "ALLOW" and d.reasons == ["PREPAID"]

    def test_BR_CR_12_prepaid_does_not_bypass_hard_overdue(self, db, customer_factory):
        c = self._customer(db, customer_factory)
        _invoice(db, c, 5000, due_days_ago=50)
        d = gate.evaluate(db, c, Money(100), is_prepaid=True, on=TODAY)
        assert d.decision == "BLOCK"   # age rule outranks prepayment

    def test_BR_CR_13_over_limit_needs_approval_with_shortfall(self, db, customer_factory):
        c = self._customer(db, customer_factory, limit_rupees=1000)
        d = gate.evaluate(db, c, Money(150000), on=TODAY)   # ₹1500 vs ₹1000 limit
        assert d.decision == "NEEDS_APPROVAL"
        assert d.suggested_payment_paise == 50000            # pay the difference

    def test_BR_CR_14_utilisation_warning(self, db, customer_factory):
        c = self._customer(db, customer_factory, limit_rupees=1000)
        d = gate.evaluate(db, c, Money(85000), on=TODAY)     # 85% of limit
        assert d.decision == "WARN" and "UTILISATION_ABOVE_80" in d.reasons

    def test_BR_CR_15_soft_overdue_warns(self, db, customer_factory):
        c = self._customer(db, customer_factory)
        _invoice(db, c, 50000, due_days_ago=10)              # within hard window
        d = gate.evaluate(db, c, Money(100), on=TODAY)
        assert d.decision == "WARN" and "HAS_OVERDUE" in d.reasons

    def test_BR_CR_15_soft_block_setting_flips_to_block(self, db, customer_factory):
        c = self._customer(db, customer_factory)
        settings_registry.set_value(db, "overdue_soft_block", "true", actor_id=None)
        _invoice(db, c, 50000, due_days_ago=10)
        d = gate.evaluate(db, c, Money(100), on=TODAY)
        assert d.decision == "BLOCK" and d.reasons == ["OVERDUE_SOFT_BLOCK"]

    def test_BR_CR_16_clean_customer_allowed(self, db, customer_factory):
        c = self._customer(db, customer_factory)
        d = gate.evaluate(db, c, Money(100000), on=TODAY)
        assert d.decision == "ALLOW" and d.reasons == []

    def test_R8_hard_block_days_is_a_setting(self, db, customer_factory):
        c = self._customer(db, customer_factory)
        settings_registry.set_value(db, "hard_block_days", "60", actor_id=None)
        _invoice(db, c, 5000, due_days_ago=50)               # 50 < 60 now
        d = gate.evaluate(db, c, Money(100), on=TODAY)
        assert d.decision == "WARN"                          # not blocked any more


class TestShadowMode:
    """BR-CR-40 — the default mode. Decisions recorded, nobody blocked."""

    def test_shadow_blocks_nobody_but_records(self, db, customer_factory):
        c = customer_factory()                               # mode=shadow seeded
        _invoice(db, c, 5000, due_days_ago=50)               # would hard-block
        d = gate.evaluate(db, c, Money(100), on=TODAY)
        assert d.decision == "WARN" and d.shadow is True
        assert "SHADOW_BLOCK" in d.reasons
        events = db.query(CreditEvent).filter(CreditEvent.is_shadow.is_(True)).all()
        assert len(events) == 1
        assert events[0].detail["decision"] == "BLOCK"       # the truth is on record

    def test_BR_CR_52_manual_block_enforced_even_in_shadow(self, db, customer_factory,
                                                           staff_factory):
        c = customer_factory()
        admin = staff_factory("admin")
        credit_service.manual_block(db, c, reason="Cheque bounced twice", actor_id=admin.id)
        d = gate.evaluate(db, c, Money(100), on=TODAY)
        assert d.decision == "BLOCK"                         # human decision holds


class TestLadder:
    def test_BR_CR_41_45_steps_fire_in_sequence(self, db, customer_factory):
        _enforce(db)
        c = customer_factory()
        inv = _invoice(db, c, 100000, due_days_ago=0)        # due today
        counts = credit_service.advance_ladder(db, TODAY)
        assert counts["pre_due"] == 1 and counts["due_today"] == 1
        assert counts["warn1"] == 0

        # 3 days later
        counts = credit_service.advance_ladder(db, TODAY + timedelta(days=3))
        assert counts["warn1"] == 1 and counts["warn2"] == 0

        # day 15: block fires and the customer is blocked
        counts = credit_service.advance_ladder(db, TODAY + timedelta(days=15))
        assert counts["warn2"] == 1 and counts["block"] == 1
        db.refresh(c)
        assert c.status == "blocked" and not c.is_manual_block
        steps = {s.step for s in db.query(EscalationState).filter_by(invoice_id=inv.id)}
        assert steps == {"pre_due", "due_today", "warn1", "warn2", "block"}

    def test_BR_CR_49_rerun_fires_nothing_extra(self, db, customer_factory):
        _enforce(db)
        c = customer_factory()
        _invoice(db, c, 100000, due_days_ago=20)
        first = credit_service.advance_ladder(db, TODAY)
        assert sum(first.values()) == 5                       # all steps at once
        second = credit_service.advance_ladder(db, TODAY)
        assert sum(second.values()) == 0                      # idempotent

    def test_shadow_ladder_records_but_never_blocks(self, db, customer_factory):
        c = customer_factory()                                # shadow mode
        _invoice(db, c, 100000, due_days_ago=20)
        credit_service.advance_ladder(db, TODAY)
        db.refresh(c)
        assert c.status == "active"                           # BR-CR-40
        assert db.query(CreditEvent).filter_by(event_type="ladder_step",
                                               is_shadow=True).count() == 5

    def test_paid_invoice_never_enters_ladder(self, db, customer_factory):
        _enforce(db)
        c = customer_factory()
        _invoice(db, c, 100000, due_days_ago=20, paid_paise=100000)
        counts = credit_service.advance_ladder(db, TODAY)
        assert sum(counts.values()) == 0


class TestAutoUnblock:
    def test_BR_CR_47_payment_clears_block(self, db, customer_factory):
        _enforce(db)
        c = customer_factory()
        inv = _invoice(db, c, 100000, due_days_ago=20)
        credit_service.advance_ladder(db, TODAY)
        db.refresh(c)
        assert c.status == "blocked"
        # payment lands (P4 does this on confirmation)
        inv.amount_paid_paise = 100000
        inv.status = "paid"
        db.commit()
        credit_service.reevaluate_block_state(db, c, TODAY)
        assert c.status == "active"

    def test_BR_CR_52_manual_block_survives_payment(self, db, customer_factory,
                                                    staff_factory):
        c = customer_factory()
        admin = staff_factory("admin")
        credit_service.manual_block(db, c, reason="fraud investigation", actor_id=admin.id)
        credit_service.reevaluate_block_state(db, c, TODAY)
        assert c.status == "blocked"                          # only admin clears it


class TestOverrides:
    def test_BR_CR_50_expiry_auto_reverts(self, db, customer_factory, staff_factory):
        c = customer_factory(credit_limit_rupees=1000)
        admin = staff_factory("admin")
        credit_service.grant_override(db, c, extra_limit_paise=900000,
                                      valid_until=TODAY + timedelta(days=1),
                                      reason="one week grace", actor_id=admin.id)
        assert compute_position(db, c, TODAY).effective_limit == Money(100000 + 900000)
        credit_service.nightly_credit_run(db, TODAY + timedelta(days=2))
        assert compute_position(db, c, TODAY + timedelta(days=2)).effective_limit == Money(100000)
        assert db.query(CreditEvent).filter_by(event_type="override_expired").count() == 1

    def test_reason_mandatory(self, db, customer_factory, staff_factory):
        c = customer_factory()
        admin = staff_factory("admin")
        with pytest.raises(ValidationFailed):
            credit_service.grant_override(db, c, extra_limit_paise=100, reason="  ",
                                          valid_until=TODAY + timedelta(days=1),
                                          actor_id=admin.id)


class TestColours:
    def test_BR_CR_30_33_matrix(self, db, customer_factory):
        c = customer_factory(credit_limit_rupees=1000)
        # green: nothing outstanding
        assert colour_state(compute_position(db, c, TODAY), c.status) == "green"
        # amber via utilisation: 70%
        ledger.post_entry(db, customer=c, entry_type="invoice", debit=Money(70000))
        db.commit()
        assert colour_state(compute_position(db, c, TODAY), c.status) == "amber"
        # red via utilisation: 95%
        ledger.post_entry(db, customer=c, entry_type="invoice", debit=Money(25000))
        db.commit()
        assert colour_state(compute_position(db, c, TODAY), c.status) == "red"
        # blocked wins over everything
        c.status = "blocked"
        assert colour_state(compute_position(db, c, TODAY), c.status) == "blocked"

    def test_overdue_days_drive_colour(self, db, customer_factory):
        c = customer_factory(credit_limit_rupees=100000)
        _invoice(db, c, 1000, due_days_ago=5)
        assert colour_state(compute_position(db, c, TODAY), "active") == "amber"
        _invoice(db, c, 1000, due_days_ago=20)
        assert colour_state(compute_position(db, c, TODAY), "active") == "red"


class TestSnapshotsAndScore:
    def test_BR_CR_54_snapshot_idempotent(self, db, customer_factory):
        c = customer_factory()
        _invoice(db, c, 100000, due_days_ago=10)
        credit_service.write_snapshot(db, c, TODAY)
        credit_service.write_snapshot(db, c, TODAY)           # re-run safe
        from app.modules.credit.models import CreditSnapshot

        rows = db.query(CreditSnapshot).filter_by(customer_id=c.id, as_of=TODAY).all()
        assert len(rows) == 1
        assert rows[0].overdue_1_30_paise == 100000

    def test_BR_SCR_07_new_customer_band(self, db, customer_factory):
        from app.modules.credit.scoring import compute_score

        c = customer_factory()
        row = compute_score(db, c, TODAY)
        assert row.band == "NEW" and row.suggested_limit_paise is None

    def test_BR_SCR_05_score_never_changes_limit(self, db, customer_factory):
        from app.modules.credit.scoring import compute_score

        c = customer_factory(credit_limit_rupees=1234)
        compute_score(db, c, TODAY)
        db.refresh(c)
        assert c.credit_limit_paise == 123400                 # untouched


class TestAdminAPI:
    def test_limit_change_needs_reason_and_permission(self, client, db, customer_factory,
                                                      as_staff):
        c = customer_factory()
        r = client.patch(f"/api/v1/customers/{c.uid}/limit",
                         json={"credit_limit_paise": 5000000, "reason": "strong payer"},
                         headers=as_staff("accounts"))
        assert r.status_code == 403                            # BR-AC-03
        r = client.patch(f"/api/v1/customers/{c.uid}/limit",
                         json={"credit_limit_paise": 5000000, "reason": "strong payer"},
                         headers=as_staff("admin"))
        assert r.status_code == 200
        events = client.get(f"/api/v1/customers/{c.uid}/credit-events",
                            headers=as_staff("admin")).json()
        assert events["items"][0]["event_type"] == "limit_changed"   # BR-CR-51

    def test_dealer_sees_own_credit_status_only(self, client, db, customer_factory,
                                                dealer, as_dealer):
        mine = customer_factory()
        other = customer_factory()
        dealer.customer_id = mine.id
        db.commit()
        assert client.get(f"/api/v1/credit/customers/{mine.uid}/status",
                          headers=as_dealer).status_code == 200
        assert client.get(f"/api/v1/credit/customers/{other.uid}/status",
                          headers=as_dealer).status_code == 403

    def test_shadow_review_screen_data(self, client, db, customer_factory, as_staff):
        c = customer_factory()
        _invoice(db, c, 5000, due_days_ago=50)
        gate.evaluate(db, c, Money(100), on=TODAY)             # records shadow event
        db.commit()
        r = client.get(f"/api/v1/customers/{c.uid}/credit-events?shadow_only=true",
                       headers=as_staff("admin"))
        assert r.status_code == 200
        assert r.json()["items"][0]["is_shadow"] is True


class TestOrderIntegration:
    """The gate wired into order creation (P3-T2-12) — end to end."""

    def test_hard_overdue_customer_cannot_order(self, client, db, design_factory,
                                                customer_factory, dealer, as_dealer):
        import uuid

        _enforce(db)
        design_factory("LGR-C2", rate_rupees="100")
        c = customer_factory()
        _invoice(db, c, 5000, due_days_ago=50)
        dealer.customer_id = c.id
        db.commit()
        r = client.post("/api/v1/orders",
                        json={"items": [{"design_no": "LGR-C2", "length_ft": "7",
                                         "breadth_ft": "4", "quantity": 1}]},
                        headers={**as_dealer, "Idempotency-Key": str(uuid.uuid4())})
        assert r.status_code == 403
        body = r.json()["error"]
        assert body["code"] == "CREDIT_BLOCKED"
        assert body["details"]["overdue_invoices"][0]["days_overdue"] == 50
        assert "Please clear your outstanding" in body["message"]

    def test_over_limit_order_lands_pending_approval(self, client, db, design_factory,
                                                     customer_factory, dealer, as_dealer):
        import uuid

        _enforce(db)
        design_factory("LGR-C3", rate_rupees="100", gst_pct="0")
        c = customer_factory(credit_limit_rupees=1000)         # ₹1000 limit
        dealer.customer_id = c.id
        db.commit()
        r = client.post("/api/v1/orders",
                        json={"items": [{"design_no": "LGR-C3", "length_ft": "7",
                                         "breadth_ft": "4", "quantity": 10}]},  # ₹28,000
                        headers={**as_dealer, "Idempotency-Key": str(uuid.uuid4())})
        assert r.status_code == 201
        assert r.json()["status"] == "PENDING_APPROVAL"        # BR-CR-13

    def test_two_sequential_orders_share_one_limit(self, client, db, design_factory,
                                                   customer_factory, dealer, as_dealer):
        """BR-CR-02 in action: the second order sees the first as exposure."""
        import uuid

        _enforce(db)
        design_factory("LGR-C4", rate_rupees="100", gst_pct="0")
        c = customer_factory(credit_limit_rupees=3000)         # fits one ₹2800 order
        dealer.customer_id = c.id
        db.commit()
        body = {"items": [{"design_no": "LGR-C4", "length_ft": "7",
                           "breadth_ft": "4", "quantity": 1}]}   # ₹2800 each
        r1 = client.post("/api/v1/orders", json=body,
                         headers={**as_dealer, "Idempotency-Key": str(uuid.uuid4())})
        assert r1.json()["status"] == "CONFIRMED"
        r2 = client.post("/api/v1/orders", json=body,
                         headers={**as_dealer, "Idempotency-Key": str(uuid.uuid4())})
        assert r2.json()["status"] == "PENDING_APPROVAL"       # limit already consumed
