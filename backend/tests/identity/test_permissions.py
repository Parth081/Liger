"""RBAC matrix tests (BR-AC-01…08, R11) + core service unit tests."""
from datetime import date

import pytest

from app.core import idempotency, numbering, settings_registry
from app.core.exceptions import Conflict
from app.core.permissions import ROLE_MATRIX, role_has
from app.modules.admin.models import AuditLog, SettingHistory


class TestRoleMatrix:
    def test_BR_AC_01_super_admin_has_everything(self):
        from app.core.permissions import PERMISSIONS

        assert ROLE_MATRIX["super_admin"] == set(PERMISSIONS)

    def test_BR_AC_03_accounts_cannot_edit_rate_cards(self):
        assert not role_has("accounts", "design.write")
        assert role_has("accounts", "payment.confirm_cash")

    def test_BR_AC_04_sales_rep_cannot_change_limits_or_confirm_cash(self):
        assert not role_has("sales_rep", "customer.limit")
        assert not role_has("sales_rep", "payment.confirm_cash")
        assert role_has("sales_rep", "order.create")

    def test_BR_AC_05_production_no_money_permissions(self):
        money_perms = {p for p in ROLE_MATRIX["super_admin"]
                       if p.startswith(("payment.", "invoice.", "ledger.", "credit.", "report."))}
        assert not (ROLE_MATRIX["production"] & money_perms)

    def test_BR_AC_06_dispatch_scope(self):
        assert ROLE_MATRIX["dispatch"] == {"order.read", "order.status.dispatch"}

    def test_unknown_role_has_nothing(self):
        assert not role_has("ghost_role", "order.read")


class TestSettingsRegistry:
    """R8: values live in the DB, are typed, audited, and cached."""

    def test_seeded_defaults(self, db):
        assert settings_registry.get_decimal(db, "min_billable_sqft") == pytest.approx(11)
        assert settings_registry.get_int(db, "hard_block_days") == 45
        assert settings_registry.get_str(db, "credit_enforcement_mode") == "shadow"  # BR-CR-40
        assert settings_registry.get_bool(db, "overdue_soft_block") is False

    def test_write_records_history_and_invalidates_cache(self, db):
        settings_registry.set_value(db, "hard_block_days", "60", actor_id=None)
        assert settings_registry.get_int(db, "hard_block_days") == 60
        hist = db.query(SettingHistory).filter(SettingHistory.key == "hard_block_days").all()
        assert len(hist) == 1 and hist[0].old_value == "45" and hist[0].new_value == "60"

    def test_unknown_key_rejected(self, db):
        with pytest.raises(KeyError):
            settings_registry.get_str(db, "no_such_key")
        with pytest.raises(KeyError):
            settings_registry.set_value(db, "no_such_key", "1", actor_id=None)


class TestNumbering:
    """BR-ORD-06 / BR-TAX-05: gapless, FY-scoped."""

    def test_financial_year(self):
        assert numbering.financial_year(date(2026, 8, 1)) == "2026-27"
        assert numbering.financial_year(date(2026, 3, 31)) == "2025-26"
        assert numbering.financial_year(date(2026, 4, 1)) == "2026-27"

    def test_sequence_gapless(self, db):
        today = date(2026, 8, 1)
        assert numbering.next_order_no(db, today) == "LGR/2026-27/00001"
        assert numbering.next_order_no(db, today) == "LGR/2026-27/00002"
        db.commit()
        assert numbering.next_order_no(db, today) == "LGR/2026-27/00003"

    def test_series_independent(self, db):
        today = date(2026, 8, 1)
        numbering.next_order_no(db, today)
        assert numbering.next_invoice_no(db, today) == "LGR/INV/2026-27/00001"
        assert numbering.next_credit_note_no(db, today) == "LGR/CN/2026-27/00001"

    def test_fy_rollover_restarts(self, db):
        assert numbering.next_order_no(db, date(2026, 3, 31)) == "LGR/2025-26/00001"
        assert numbering.next_order_no(db, date(2026, 4, 1)) == "LGR/2026-27/00001"


class TestIdempotency:
    """R6: one effect per key; changed payload is a client error."""

    def test_first_call_proceeds_then_replays(self, db):
        replay = idempotency.begin(db, "key-1", "POST /orders", {"a": 1})
        assert replay is None
        idempotency.store(db, "key-1", 201, {"order_no": "LGR/2026-27/00001"})
        db.commit()

        replay2 = idempotency.begin(db, "key-1", "POST /orders", {"a": 1})
        assert replay2 == {"status_code": 201, "body": {"order_no": "LGR/2026-27/00001"}}

    def test_same_key_different_payload_conflict(self, db):
        idempotency.begin(db, "key-2", "POST /orders", {"a": 1})
        idempotency.store(db, "key-2", 201, {"ok": True})
        db.commit()
        with pytest.raises(Conflict):
            idempotency.begin(db, "key-2", "POST /orders", {"a": 2})

    def test_in_flight_key_conflicts(self, db):
        idempotency.begin(db, "key-3", "POST /orders", {"a": 1})
        db.commit()
        with pytest.raises(Conflict):
            idempotency.begin(db, "key-3", "POST /orders", {"a": 1})


class TestAudit:
    def test_login_writes_audit(self, client, super_admin, db):
        client.post("/api/v1/auth/staff/login",
                    json={"email": "owner@ligertest.com", "password": "owner-pass-123"})
        rows = db.query(AuditLog).filter(AuditLog.action == "auth.login").all()
        assert len(rows) == 1
        assert rows[0].actor_type == "user"
