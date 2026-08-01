"""P5 tests: rendering in 3 languages (BR-NOT-07), dedupe/caps/quiet hours
(BR-NOT-05/06), fallback (BR-NOT-02), ladder sends exactly once (BR-CR-49),
admin fan-out (BR-NOT-03), opt-out rules (BR-NOT-09)."""
from datetime import date, timedelta

import pytest

from app.core import settings_registry
from app.core.exceptions import ValidationFailed
from app.modules.notifications import dispatch
from app.modules.notifications.models import Notification, NotificationPreference
from app.modules.notifications.providers import ConsoleProvider, reset_providers
from app.modules.notifications.templates import LANGUAGES, TEMPLATES, render

TODAY = date(2026, 8, 1)


@pytest.fixture(autouse=True)
def _providers():
    reset_providers()
    yield
    reset_providers()


@pytest.fixture(autouse=True)
def _daytime(monkeypatch):
    """Pin dispatch outside quiet hours unless a test overrides."""
    monkeypatch.setattr(dispatch, "_in_quiet_hours", lambda db, now: False)


_FULL_VARS = {
    "order_no": "LGR/2026-27/00001", "item_count": "3", "total": "₹1,000.00",
    "expected_delivery": "2026-08-10", "status": "READY", "extra": "",
    "invoice_no": "LGR/INV/1", "due_date": "2026-08-15", "pay_link": "https://pay",
    "amount": "₹500.00", "days_overdue": "5", "days_to_block": "5",
    "outstanding": "₹500.00", "available": "₹1,000.00", "method": "cash",
    "staff": "Ravi", "customer": "Test Traders",
    "orders_count": "4", "orders_value": "₹1", "collections": "₹2", "new_blocks": "0",
}


class TestTemplates:
    def test_BR_NOT_07_every_template_renders_in_all_languages(self, db):
        for key in TEMPLATES:
            for lang in LANGUAGES:
                body, _ = render(db, key, "whatsapp", lang, _FULL_VARS)
                assert "{{" not in body, f"{key}/{lang} left variables unresolved"

    def test_BR_NOT_08_missing_variable_is_an_error(self, db):
        with pytest.raises(ValidationFailed) as e:
            render(db, "credit.warn1", "whatsapp", "en", {"invoice_no": "X"})
        assert "missing variables" in e.value.message

    def test_unknown_language_falls_back_to_english(self, db):
        body, _ = render(db, "order.placed", "whatsapp", "fr", _FULL_VARS)
        assert body.startswith("Liger: Order")


class TestDispatch:
    def test_send_and_language_selection(self, db, customer_factory):
        c = customer_factory()
        c.language = "hi"
        db.commit()
        n = dispatch.notify_customer(db, customer=c, template_key="order.placed",
                                     variables=_FULL_VARS, dedupe_key="t1")
        assert n is not None and n.status == "sent"
        assert "ऑर्डर" in n.rendered_body                      # Hindi body

    def test_BR_NOT_06_dedupe(self, db, customer_factory):
        c = customer_factory()
        first = dispatch.notify_customer(db, customer=c, template_key="order.placed",
                                         variables=_FULL_VARS, dedupe_key="dup")
        second = dispatch.notify_customer(db, customer=c, template_key="order.placed",
                                          variables=_FULL_VARS, dedupe_key="dup")
        assert first is not None and second is None
        assert db.query(Notification).count() == 1

    def test_BR_NOT_06_daily_cap(self, db, customer_factory):
        c = customer_factory()
        settings_registry.set_value(db, "max_msgs_per_customer_per_day", "2", actor_id=None)
        for i in range(2):
            assert dispatch.notify_customer(db, customer=c, template_key="order.placed",
                                            variables=_FULL_VARS, dedupe_key=f"cap{i}") is not None
        assert dispatch.notify_customer(db, customer=c, template_key="order.placed",
                                        variables=_FULL_VARS, dedupe_key="cap3") is None
        # critical messages are exempt from the cap
        assert dispatch.notify_customer(db, customer=c, template_key="credit.blocked",
                                        variables=_FULL_VARS, dedupe_key="cap4",
                                        critical=True) is not None

    def test_BR_NOT_05_quiet_hours_defer_noncritical(self, db, customer_factory,
                                                     monkeypatch):
        monkeypatch.setattr(dispatch, "_in_quiet_hours", lambda db, now: True)
        c = customer_factory()
        n = dispatch.notify_customer(db, customer=c, template_key="order.placed",
                                     variables=_FULL_VARS, dedupe_key="q1")
        assert n is not None and n.status == "deferred" and n.scheduled_for is not None
        # block notices still go out at night
        n2 = dispatch.notify_customer(db, customer=c, template_key="credit.blocked",
                                      variables=_FULL_VARS, dedupe_key="q2", critical=True)
        assert n2 is not None and n2.status == "sent"

    def test_BR_NOT_02_fallback_to_sms_on_whatsapp_failure(self, db, customer_factory,
                                                           monkeypatch):
        from app.modules.notifications import providers as providers_module
        from app.modules.notifications.providers import SendResult

        class FailingWhatsApp(ConsoleProvider):
            def send(self, **kwargs):
                return SendResult(ok=False, error="provider down")

        failing = FailingWhatsApp("whatsapp")
        working_sms = ConsoleProvider("sms")
        monkeypatch.setattr(providers_module, "_providers",
                            {"whatsapp": failing, "sms": working_sms})
        c = customer_factory()
        n = dispatch.notify_customer(db, customer=c, template_key="order.placed",
                                     variables=_FULL_VARS, dedupe_key="fb1")
        assert n is not None
        assert n.channel == "sms" and n.status == "sent"       # fell back
        assert len(working_sms.sent) == 1

    def test_dead_letter_after_max_attempts(self, db, customer_factory, monkeypatch):
        from app.modules.notifications import providers as providers_module
        from app.modules.notifications.providers import SendResult

        class AlwaysFail(ConsoleProvider):
            def send(self, **kwargs):
                return SendResult(ok=False, error="down")

        monkeypatch.setattr(providers_module, "_providers",
                            {"whatsapp": AlwaysFail("whatsapp"), "sms": AlwaysFail("sms")})
        c = customer_factory()
        prefs = NotificationPreference(customer_id=c.id, sms_enabled=False)
        db.add(prefs)
        db.commit()
        n = dispatch.notify_customer(db, customer=c, template_key="order.placed",
                                     variables=_FULL_VARS, dedupe_key="dl1")
        assert n is not None and n.status == "failed"
        for _ in range(6):
            dispatch.attempt_send(db, n)
        assert n.status == "dead"                              # BR-NOT-02 dead letter

    def test_BR_NOT_09_marketing_optout_but_transactional_delivers(self, db,
                                                                   customer_factory):
        c = customer_factory()
        db.add(NotificationPreference(customer_id=c.id, marketing_opt_out=True))
        db.commit()
        assert dispatch.notify_customer(db, customer=c, template_key="order.placed",
                                        variables=_FULL_VARS, dedupe_key="m1",
                                        is_marketing=True) is None
        assert dispatch.notify_customer(db, customer=c, template_key="credit.blocked",
                                        variables=_FULL_VARS, dedupe_key="m2") is not None


class TestLadderSends:
    def _setup(self, db, customer_factory):
        from app.modules.credit import service as credit_service
        from app.modules.credit.models import Invoice

        settings_registry.set_value(db, "credit_enforcement_mode", "enforce", actor_id=None)
        c = customer_factory()
        inv = Invoice(invoice_no=f"INV-N-{c.id}", customer_id=c.id,
                      invoice_date=TODAY - timedelta(days=50),
                      due_date=TODAY - timedelta(days=20),
                      total_paise=100000, status="open")
        db.add(inv)
        db.commit()
        credit_service.advance_ladder(db, TODAY)
        return c, inv

    def test_BR_CR_49_each_step_sends_exactly_once(self, db, customer_factory):
        from app.modules.notifications.hooks import send_ladder_notifications

        c, inv = self._setup(db, customer_factory)
        sent = send_ladder_notifications(db, TODAY)
        assert sent == 5                                        # all 5 steps fired at day 20
        again = send_ladder_notifications(db, TODAY)
        assert again == 0                                       # idempotent
        keys = {n.dedupe_key for n in db.query(Notification)
                .filter(Notification.customer_id == c.id)}
        assert f"ladder:{inv.id}:block" in keys

    def test_shadow_mode_sends_nothing_to_dealers(self, db, customer_factory):
        from app.modules.credit import service as credit_service
        from app.modules.credit.models import Invoice
        from app.modules.notifications.hooks import send_ladder_notifications

        c = customer_factory()                                  # shadow (default)
        db.add(Invoice(invoice_no=f"INV-S-{c.id}", customer_id=c.id,
                       invoice_date=TODAY - timedelta(days=50),
                       due_date=TODAY - timedelta(days=20),
                       total_paise=100000, status="open"))
        db.commit()
        credit_service.advance_ladder(db, TODAY)
        assert send_ladder_notifications(db, TODAY) == 0        # BR-CR-40


class TestEventWiring:
    def test_order_placed_notifies_dealer(self, client, db, design_factory,
                                          customer_factory, dealer, as_dealer):
        import uuid

        design_factory("LGR-N1", rate_rupees="100")
        c = customer_factory()
        dealer.customer_id = c.id
        db.commit()
        r = client.post("/api/v1/orders",
                        json={"items": [{"design_no": "LGR-N1", "length_ft": "7",
                                         "breadth_ft": "4", "quantity": 1}]},
                        headers={**as_dealer, "Idempotency-Key": str(uuid.uuid4())})
        assert r.status_code == 201
        rows = db.query(Notification).filter(Notification.customer_id == c.id,
                                             Notification.template_key == "order.placed").all()
        assert len(rows) == 1
        assert r.json()["order_no"] in rows[0].rendered_body

    def test_cash_pending_notifies_admins(self, client, db, customer_factory,
                                          as_staff, staff_factory):
        admin = staff_factory("admin")
        admin.phone = "+919000000001"
        db.commit()
        c = customer_factory()
        r = client.post("/api/v1/payments/offline",
                        json={"customer_uid": str(c.uid), "amount_paise": 50000,
                              "method": "cash"},
                        headers=as_staff("accounts"))
        assert r.status_code == 201
        rows = db.query(Notification).filter(
            Notification.template_key == "payment.cash_pending").all()
        assert len(rows) >= 1 and rows[0].user_id == admin.id   # BR-NOT-03 admin fan-out


class TestAdminAPI:
    def test_log_and_test_send(self, client, db, customer_factory, as_staff):
        c = customer_factory()
        dispatch.notify_customer(db, customer=c, template_key="order.placed",
                                 variables=_FULL_VARS, dedupe_key="api1")
        r = client.get("/api/v1/notifications", headers=as_staff("admin"))
        assert r.status_code == 200 and len(r.json()["items"]) == 1
        r = client.post("/api/v1/notifications/test-send",
                        json={"template_key": "credit.warn2_final", "variables": _FULL_VARS},
                        headers=as_staff("super_admin"))
        previews = r.json()["previews"]
        assert set(previews) == {"en", "hi", "gu"}
        assert "BLOCKED" in previews["en"]

    def test_dealer_preferences_roundtrip(self, client, db, customer_factory, dealer,
                                          as_dealer):
        c = customer_factory()
        dealer.customer_id = c.id
        db.commit()
        r = client.patch("/api/v1/me/notification-preferences",
                         json={"marketing_opt_out": True}, headers=as_dealer)
        assert r.status_code == 200
        prefs = client.get("/api/v1/me/notification-preferences", headers=as_dealer).json()
        assert prefs["marketing_opt_out"] is True
