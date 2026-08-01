"""P6 tests: lifecycle + role gates, BR-CR-46 (blocked but in-flight completes),
delivery→follow-up chain, task board, nightly generators."""
import uuid
from datetime import date, timedelta

from app.core import settings_registry
from app.modules.credit import service as credit_service
from app.modules.credit.models import Invoice
from app.modules.fulfilment import service
from app.modules.fulfilment.models import Delivery, Dispatch, FollowUpTask
from app.modules.orders.models import Order

TODAY = date(2026, 8, 1)


def _idem():
    return {"Idempotency-Key": str(uuid.uuid4())}


def _order(client, db, design_factory, customer_factory, as_staff, design_no="LGR-F1",
           customer=None):
    design_factory(design_no, rate_rupees="100", gst_pct="0")
    c = customer or customer_factory()
    r = client.post("/api/v1/orders",
                    json={"customer_uid": str(c.uid),
                          "items": [{"design_no": design_no, "length_ft": "7",
                                     "breadth_ft": "4", "quantity": 1}]},
                    headers={**as_staff("admin"), **_idem()})
    return c, r.json()


class TestLifecycle:
    def test_full_path_with_role_gates(self, client, db, design_factory,
                                       customer_factory, as_staff):
        _, order = _order(client, db, design_factory, customer_factory, as_staff)
        production = as_staff("production")
        dispatch = as_staff("dispatch")

        assert client.post(f"/api/v1/orders/{order['uid']}/production/start", json={},
                           headers=production).json()["status"] == "IN_PRODUCTION"
        assert client.post(f"/api/v1/orders/{order['uid']}/production/ready",
                           headers=production).json()["status"] == "READY"
        r = client.post(f"/api/v1/orders/{order['uid']}/dispatch",
                        json={"transporter": "VRL", "lr_no": "LR-9981",
                              "vehicle_no": "GJ01AB1234"},
                        headers=dispatch)
        assert r.json()["status"] == "DISPATCHED" and r.json()["lr_no"] == "LR-9981"
        r = client.post(f"/api/v1/orders/{order['uid']}/deliver",
                        json={"received_by": "Site supervisor",
                              "pod_image_url": "https://cdn/pod.jpg"},
                        headers=dispatch)
        assert r.json()["status"] == "DELIVERED"

        assert db.query(Dispatch).count() == 1
        delivery = db.query(Delivery).one()
        assert delivery.pod_image_url == "https://cdn/pod.jpg"

    def test_dispatch_role_cannot_run_production(self, client, db, design_factory,
                                                 customer_factory, as_staff):
        _, order = _order(client, db, design_factory, customer_factory, as_staff)
        r = client.post(f"/api/v1/orders/{order['uid']}/production/start", json={},
                        headers=as_staff("dispatch"))
        assert r.status_code == 403

    def test_BR_ORD_02_cannot_dispatch_before_ready(self, client, db, design_factory,
                                                    customer_factory, as_staff):
        _, order = _order(client, db, design_factory, customer_factory, as_staff)
        r = client.post(f"/api/v1/orders/{order['uid']}/dispatch", json={"lr_no": "X"},
                        headers=as_staff("dispatch"))
        assert r.status_code == 409

    def test_partial_delivery_state(self, client, db, design_factory, customer_factory,
                                    as_staff):
        _, order = _order(client, db, design_factory, customer_factory, as_staff)
        production, dispatch = as_staff("production"), as_staff("dispatch")
        client.post(f"/api/v1/orders/{order['uid']}/production/start", json={},
                    headers=production)
        client.post(f"/api/v1/orders/{order['uid']}/production/ready", headers=production)
        client.post(f"/api/v1/orders/{order['uid']}/dispatch", json={}, headers=dispatch)
        r = client.post(f"/api/v1/orders/{order['uid']}/deliver",
                        json={"is_partial": True, "received_by": "partial"},
                        headers=dispatch)
        assert r.json()["status"] == "PARTIALLY_DELIVERED"


class TestBlockedButInFlight:
    def test_BR_CR_46_in_production_order_completes_while_new_orders_refused(
            self, client, db, design_factory, customer_factory, as_staff, dealer,
            as_dealer):
        """The rule that keeps the factory running while collection is chased."""
        settings_registry.set_value(db, "credit_enforcement_mode", "enforce", actor_id=None)
        c, order = _order(client, db, design_factory, customer_factory, as_staff)
        production = as_staff("production")
        client.post(f"/api/v1/orders/{order['uid']}/production/start", json={},
                    headers=production)

        # customer goes overdue and is auto-blocked mid-production
        db.add(Invoice(invoice_no=f"INV-46-{c.id}", customer_id=c.id,
                       invoice_date=TODAY - timedelta(days=50),
                       due_date=TODAY - timedelta(days=20),
                       total_paise=100000, status="open"))
        db.commit()
        credit_service.advance_ladder(db, TODAY)
        db.refresh(c)
        assert c.status == "blocked"

        # the in-flight order still completes end to end
        assert client.post(f"/api/v1/orders/{order['uid']}/production/ready",
                           headers=production).status_code == 200
        dispatch = as_staff("dispatch")
        assert client.post(f"/api/v1/orders/{order['uid']}/dispatch", json={"lr_no": "LR-1"},
                           headers=dispatch).status_code == 200
        assert client.post(f"/api/v1/orders/{order['uid']}/deliver",
                           json={"received_by": "Owner"},
                           headers=dispatch).json()["status"] == "DELIVERED"

        # but a NEW order is refused
        dealer.customer_id = c.id
        db.commit()
        r = client.post("/api/v1/orders",
                        json={"items": [{"design_no": "LGR-F1", "length_ft": "7",
                                         "breadth_ft": "4", "quantity": 1}]},
                        headers={**as_dealer, **_idem()})
        assert r.status_code == 403


class TestDeliveryFollowUp:
    def test_unpaid_delivery_creates_task(self, client, db, design_factory,
                                          customer_factory, as_staff):
        c, order = _order(client, db, design_factory, customer_factory, as_staff)
        production, dispatch = as_staff("production"), as_staff("dispatch")
        client.post(f"/api/v1/orders/{order['uid']}/production/start", json={},
                    headers=production)
        client.post(f"/api/v1/orders/{order['uid']}/production/ready", headers=production)
        client.post(f"/api/v1/orders/{order['uid']}/dispatch", json={}, headers=dispatch)
        client.post(f"/api/v1/orders/{order['uid']}/deliver", json={"received_by": "X"},
                    headers=dispatch)

        task = db.query(FollowUpTask).filter(FollowUpTask.type == "delivery_unpaid").one()
        assert order["order_no"] in task.title
        assert task.status == "open"

    def test_task_assigned_to_customers_sales_rep(self, client, db, design_factory,
                                                  customer_factory, as_staff,
                                                  staff_factory):
        rep = staff_factory("sales_rep")
        c = customer_factory()
        c.sales_rep_id = rep.id
        db.commit()
        _, order = _order(client, db, design_factory, customer_factory, as_staff,
                          customer=c)
        production, dispatch = as_staff("production"), as_staff("dispatch")
        client.post(f"/api/v1/orders/{order['uid']}/production/start", json={},
                    headers=production)
        client.post(f"/api/v1/orders/{order['uid']}/production/ready", headers=production)
        client.post(f"/api/v1/orders/{order['uid']}/dispatch", json={}, headers=dispatch)
        client.post(f"/api/v1/orders/{order['uid']}/deliver", json={}, headers=dispatch)
        task = db.query(FollowUpTask).filter(FollowUpTask.type == "delivery_unpaid").one()
        assert task.assignee_id == rep.id          # P6-T2-06


class TestNightlyGenerators:
    def test_warn2_creates_call_task(self, db, customer_factory):
        settings_registry.set_value(db, "credit_enforcement_mode", "enforce", actor_id=None)
        c = customer_factory()
        db.add(Invoice(invoice_no=f"INV-W2-{c.id}", customer_id=c.id,
                       invoice_date=TODAY - timedelta(days=50),
                       due_date=TODAY - timedelta(days=12),
                       total_paise=250000, status="open"))
        db.commit()
        credit_service.advance_ladder(db, TODAY)
        created = service.generate_warn2_tasks(db, TODAY)
        assert created == 1
        task = db.query(FollowUpTask).filter(FollowUpTask.type == "payment_chase").one()
        assert "final warning" in task.title
        # BR-AN-06: re-running the generator does not duplicate
        assert service.generate_warn2_tasks(db, TODAY) == 0

    def test_reorder_gap_uses_customers_own_rhythm(self, db, customer_factory):
        """A monthly buyer silent 60 days is chased; a quarterly buyer is not."""
        monthly = customer_factory()
        quarterly = customer_factory()
        for i in range(12):                                  # every ~30 days
            db.add(Order(order_no=f"M/{monthly.id}/{i}", customer_id=monthly.id,
                         placed_by_type="user", placed_by_id=1, channel="staff",
                         status="DELIVERED",
                         order_date=TODAY - timedelta(days=60 + 30 * i),
                         subtotal_paise=1000, taxable_paise=1000, grand_total_paise=1000))
        for i in range(4):                                   # every ~90 days
            db.add(Order(order_no=f"Q/{quarterly.id}/{i}", customer_id=quarterly.id,
                         placed_by_type="user", placed_by_id=1, channel="staff",
                         status="DELIVERED",
                         order_date=TODAY - timedelta(days=60 + 90 * i),
                         subtotal_paise=1000, taxable_paise=1000, grand_total_paise=1000))
        db.commit()
        service.generate_reorder_gap_tasks(db, TODAY)
        chased = {t.customer_id for t in
                  db.query(FollowUpTask).filter(FollowUpTask.type == "reorder_gap")}
        assert monthly.id in chased
        assert quarterly.id not in chased        # 60 days is normal for them

    def test_nightly_bundle_idempotent(self, db, customer_factory):
        settings_registry.set_value(db, "credit_enforcement_mode", "enforce", actor_id=None)
        c = customer_factory()
        db.add(Invoice(invoice_no=f"INV-NB-{c.id}", customer_id=c.id,
                       invoice_date=TODAY - timedelta(days=50),
                       due_date=TODAY - timedelta(days=12),
                       total_paise=100000, status="open"))
        db.commit()
        credit_service.advance_ladder(db, TODAY)
        first = service.nightly_followups(db, TODAY)
        second = service.nightly_followups(db, TODAY)
        assert first["warn2"] == 1 and second["warn2"] == 0


class TestTaskBoard:
    def test_board_scoping_and_close(self, client, db, customer_factory, as_staff,
                                     staff_factory):
        rep = staff_factory("sales_rep")
        c = customer_factory()
        c.sales_rep_id = rep.id
        db.commit()
        r = client.post("/api/v1/follow-ups",
                        json={"customer_uid": str(c.uid), "title": "Call about payment",
                              "due_date": str(TODAY), "assignee_id": rep.id},
                        headers=as_staff("admin"))
        assert r.status_code == 201
        task_uid = r.json()["uid"]

        # rep sees only their own tasks (BR-AC-04)
        rep_headers = as_staff("sales_rep")
        listing = client.get("/api/v1/follow-ups", headers=rep_headers).json()
        assert len(listing["items"]) == 1

        r = client.post(f"/api/v1/follow-ups/{task_uid}/close",
                        json={"outcome": "Promised payment on Friday"},
                        headers=rep_headers)
        assert r.status_code == 200 and r.json()["status"] == "done"
        assert r.json()["outcome"] == "Promised payment on Friday"

    def test_close_requires_outcome(self, client, db, customer_factory, as_staff):
        c = customer_factory()
        task_uid = client.post("/api/v1/follow-ups",
                               json={"customer_uid": str(c.uid), "title": "Call them",
                                     "due_date": str(TODAY)},
                               headers=as_staff("admin")).json()["uid"]
        r = client.post(f"/api/v1/follow-ups/{task_uid}/close", json={"outcome": "x"},
                        headers=as_staff("admin"))
        assert r.status_code == 400
