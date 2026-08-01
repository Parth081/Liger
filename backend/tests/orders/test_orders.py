"""P2 order tests: totals==quote (BR-PR-11), idempotency (R6/BR-ORD-07),
snapshots (R9), state machine (BR-ORD-02), scoping (BR-AC-07)."""
import uuid

from app.core import events


def _order_body(items=None, **over):
    body = {
        "items": items or [
            {"design_no": "LGR-O1", "length_ft": "7", "breadth_ft": "4", "quantity": 1},
            {"design_no": "LGR-O1", "length_ft": "3", "breadth_ft": "3", "quantity": 4,
             "room_label": "Bedroom 1"},
        ],
    }
    body.update(over)
    return body


def _idem():
    return {"Idempotency-Key": str(uuid.uuid4())}


class TestOrderCreation:
    def _setup(self, db, design_factory, customer_factory):
        design_factory("LGR-O1", rate_rupees="100", gst_pct="12")
        return customer_factory(state="GJ")

    def test_dealer_places_order(self, client, db, design_factory, customer_factory,
                                 dealer, as_dealer):
        customer = self._setup(db, design_factory, customer_factory)
        dealer.customer_id = customer.id
        db.commit()
        events.capture_mode = True
        events.reset()
        r = client.post("/api/v1/orders", json=_order_body(), headers={**as_dealer, **_idem()})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["order_no"].startswith("LGR/2026-27/")
        assert body["status"] == "CONFIRMED"
        # 28 sqft ×100 + 44 sqft ×100 = ₹7200; GST 12% = ₹864 → ₹8064
        assert body["grand_total_paise"] == 806400
        assert ("order.placed", ) [0] in [e[0] for e in events.captured]  # R10 event emitted
        events.capture_mode = False

    def test_BR_PR_11_order_total_equals_quote(self, client, db, design_factory,
                                               customer_factory, dealer, as_dealer):
        customer = self._setup(db, design_factory, customer_factory)
        dealer.customer_id = customer.id
        db.commit()
        quote = client.post("/api/v1/pricing/quote-cart",
                            json={"items": _order_body()["items"], "customer_state": "GJ"},
                            headers=as_dealer).json()
        order = client.post("/api/v1/orders", json=_order_body(),
                            headers={**as_dealer, **_idem()}).json()
        assert order["grand_total_paise"] == quote["grand_total_paise"]

    def test_BR_ORD_07_idempotent_replay(self, client, db, design_factory, customer_factory,
                                         dealer, as_dealer):
        customer = self._setup(db, design_factory, customer_factory)
        dealer.customer_id = customer.id
        db.commit()
        key = _idem()
        r1 = client.post("/api/v1/orders", json=_order_body(), headers={**as_dealer, **key})
        r2 = client.post("/api/v1/orders", json=_order_body(), headers={**as_dealer, **key})
        assert r1.json()["order_no"] == r2.json()["order_no"]
        from app.modules.orders.models import Order

        assert db.query(Order).count() == 1  # one effect, not two

    def test_missing_idempotency_key_rejected(self, client, db, design_factory,
                                              customer_factory, dealer, as_dealer):
        customer = self._setup(db, design_factory, customer_factory)
        dealer.customer_id = customer.id
        db.commit()
        r = client.post("/api/v1/orders", json=_order_body(), headers=as_dealer)
        assert r.status_code == 400  # R6: header required

    def test_R9_snapshot_survives_rate_change(self, client, db, design_factory,
                                              customer_factory, dealer, as_dealer):
        customer = self._setup(db, design_factory, customer_factory)
        dealer.customer_id = customer.id
        db.commit()
        order = client.post("/api/v1/orders", json=_order_body(),
                            headers={**as_dealer, **_idem()}).json()
        # rate changes AFTER the order
        from app.modules.catalog.models import Design

        d = db.query(Design).filter(Design.design_no == "LGR-O1").one()
        d.base_rate_paise = 99900
        db.commit()
        detail = client.get(f"/api/v1/orders/{order['uid']}", headers=as_dealer).json()
        assert detail["grand_total_paise"] == order["grand_total_paise"]  # frozen (R9)
        assert all(i["rate_paise"] == 10000 for i in detail["items"])

    def test_BR_SQFT_07_min_rule_visible_on_order(self, client, db, design_factory,
                                                  customer_factory, dealer, as_dealer):
        customer = self._setup(db, design_factory, customer_factory)
        dealer.customer_id = customer.id
        db.commit()
        order = client.post("/api/v1/orders", json=_order_body(),
                            headers={**as_dealer, **_idem()}).json()
        detail = client.get(f"/api/v1/orders/{order['uid']}", headers=as_dealer).json()
        flags = [i["min_rule_applied"] for i in detail["items"]]
        assert flags == [False, True]

    def test_room_labels_stored(self, client, db, design_factory, customer_factory,
                                dealer, as_dealer):
        customer = self._setup(db, design_factory, customer_factory)
        dealer.customer_id = customer.id
        db.commit()
        order = client.post("/api/v1/orders", json=_order_body(),
                            headers={**as_dealer, **_idem()}).json()
        detail = client.get(f"/api/v1/orders/{order['uid']}", headers=as_dealer).json()
        assert detail["items"][1]["room_label"] == "Bedroom 1"

    def test_manual_block_stops_order_even_in_P2(self, client, db, design_factory,
                                                 customer_factory, dealer, as_dealer):
        """BR-CR-52: even the stub gate honours a manual block."""
        customer = self._setup(db, design_factory, customer_factory)
        customer.status = "blocked"
        customer.is_manual_block = True
        dealer.customer_id = customer.id
        db.commit()
        r = client.post("/api/v1/orders", json=_order_body(), headers={**as_dealer, **_idem()})
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "CREDIT_BLOCKED"


class TestStaffOnBehalf:
    def test_BR_ORD_08_staff_orders_for_customer(self, client, db, design_factory,
                                                 customer_factory, as_staff):
        design_factory("LGR-O1", rate_rupees="100")
        customer = customer_factory()
        r = client.post("/api/v1/orders",
                        json=_order_body(customer_uid=str(customer.uid)),
                        headers={**as_staff("admin"), **_idem()})
        assert r.status_code == 201
        assert r.json()["channel"] == "staff"

    def test_staff_without_customer_uid_rejected(self, client, db, design_factory, as_staff):
        design_factory("LGR-O1")
        r = client.post("/api/v1/orders", json=_order_body(),
                        headers={**as_staff("admin"), **_idem()})
        assert r.status_code == 400

    def test_BR_AC_04_rep_cannot_order_for_unassigned_customer(self, client, db,
                                                               design_factory,
                                                               customer_factory, as_staff):
        design_factory("LGR-O1")
        customer = customer_factory()  # no sales_rep assigned
        r = client.post("/api/v1/orders",
                        json=_order_body(customer_uid=str(customer.uid)),
                        headers={**as_staff("sales_rep"), **_idem()})
        assert r.status_code == 403

    def test_production_role_cannot_create(self, client, db, design_factory,
                                           customer_factory, as_staff):
        design_factory("LGR-O1")
        customer = customer_factory()
        r = client.post("/api/v1/orders",
                        json=_order_body(customer_uid=str(customer.uid)),
                        headers={**as_staff("production"), **_idem()})
        assert r.status_code == 403  # BR-AC-05


class TestScoping:
    def test_BR_AC_07_dealer_sees_only_own_orders(self, client, db, design_factory,
                                                  customer_factory, dealer, as_dealer, as_staff):
        design_factory("LGR-O1", rate_rupees="100")
        mine = customer_factory()
        other = customer_factory()
        dealer.customer_id = mine.id
        db.commit()
        admin = as_staff("admin")
        client.post("/api/v1/orders", json=_order_body(customer_uid=str(mine.uid)),
                    headers={**admin, **_idem()})
        foreign = client.post("/api/v1/orders", json=_order_body(customer_uid=str(other.uid)),
                              headers={**admin, **_idem()}).json()
        listing = client.get("/api/v1/orders", headers=as_dealer).json()
        assert len(listing["items"]) == 1
        # foreign order is invisible, not forbidden — existence is not revealed
        r = client.get(f"/api/v1/orders/{foreign['uid']}", headers=as_dealer)
        assert r.status_code == 404

    def test_BR_AC_05_production_sees_no_money(self, client, db, design_factory,
                                               customer_factory, as_staff):
        design_factory("LGR-O1", rate_rupees="100")
        customer = customer_factory()
        order = client.post("/api/v1/orders", json=_order_body(customer_uid=str(customer.uid)),
                            headers={**as_staff("admin"), **_idem()}).json()
        detail = client.get(f"/api/v1/orders/{order['uid']}",
                            headers=as_staff("production")).json()
        assert not any(k.endswith("_paise") for k in detail)
        assert all(not any(k.endswith("_paise") for k in item) for item in detail["items"])
        assert "credit_decision" not in detail
        # specs are still there — that's the job
        assert detail["items"][0]["billable_sqft"] > 0


class TestStateMachine:
    def _confirmed_order(self, client, db, design_factory, customer_factory, as_staff):
        design_factory("LGR-O1", rate_rupees="100")
        customer = customer_factory()
        return client.post("/api/v1/orders", json=_order_body(customer_uid=str(customer.uid)),
                           headers={**as_staff("admin"), **_idem()}).json()

    def test_BR_ORD_02_illegal_jump_rejected(self, client, db, design_factory,
                                             customer_factory, as_staff):
        order = self._confirmed_order(client, db, design_factory, customer_factory, as_staff)
        r = client.post(f"/api/v1/orders/{order['uid']}/status",
                        json={"to_status": "DELIVERED"}, headers=as_staff("admin"))
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "INVALID_TRANSITION"

    def test_full_happy_path_with_role_gates(self, client, db, design_factory,
                                             customer_factory, as_staff):
        order = self._confirmed_order(client, db, design_factory, customer_factory, as_staff)
        production = as_staff("production")
        dispatch = as_staff("dispatch")
        for to, headers in [("IN_PRODUCTION", production), ("READY", production),
                            ("DISPATCHED", dispatch), ("DELIVERED", dispatch)]:
            r = client.post(f"/api/v1/orders/{order['uid']}/status",
                            json={"to_status": to}, headers=headers)
            assert r.status_code == 200, f"{to}: {r.text}"

    def test_role_gate_enforced(self, client, db, design_factory, customer_factory, as_staff):
        order = self._confirmed_order(client, db, design_factory, customer_factory, as_staff)
        # dispatch role cannot run production transitions
        r = client.post(f"/api/v1/orders/{order['uid']}/status",
                        json={"to_status": "IN_PRODUCTION"}, headers=as_staff("dispatch"))
        assert r.status_code == 403

    def test_BR_ORD_03_history_written(self, client, db, design_factory,
                                       customer_factory, as_staff):
        order = self._confirmed_order(client, db, design_factory, customer_factory, as_staff)
        client.post(f"/api/v1/orders/{order['uid']}/status",
                    json={"to_status": "IN_PRODUCTION"}, headers=as_staff("production"))
        detail = client.get(f"/api/v1/orders/{order['uid']}", headers=as_staff("admin")).json()
        transitions = [(h["from"], h["to"]) for h in detail["status_history"]]
        assert transitions == [(None, "CONFIRMED"), ("CONFIRMED", "IN_PRODUCTION")]

    def test_BR_ORD_09_cancel_needs_reason_and_permission(self, client, db, design_factory,
                                                          customer_factory, as_staff):
        order = self._confirmed_order(client, db, design_factory, customer_factory, as_staff)
        r = client.post(f"/api/v1/orders/{order['uid']}/cancel",
                        json={"reason": "It"}, headers=as_staff("admin"))
        assert r.status_code == 400  # too short
        r = client.post(f"/api/v1/orders/{order['uid']}/cancel",
                        json={"reason": "Customer changed mind"}, headers=as_staff("accounts"))
        assert r.status_code == 403  # accounts lacks order.cancel
        r = client.post(f"/api/v1/orders/{order['uid']}/cancel",
                        json={"reason": "Customer changed mind"}, headers=as_staff("admin"))
        assert r.status_code == 200

    def test_BR_ORD_09_no_cancel_once_in_production(self, client, db, design_factory,
                                                    customer_factory, as_staff):
        order = self._confirmed_order(client, db, design_factory, customer_factory, as_staff)
        client.post(f"/api/v1/orders/{order['uid']}/status",
                    json={"to_status": "IN_PRODUCTION"}, headers=as_staff("production"))
        r = client.post(f"/api/v1/orders/{order['uid']}/cancel",
                        json={"reason": "Too late now"}, headers=as_staff("admin"))
        assert r.status_code == 409


class TestQuotations:
    def test_BR_ORD_05_quote_then_convert(self, client, db, design_factory,
                                          customer_factory, dealer, as_dealer):
        design_factory("LGR-O1", rate_rupees="100", gst_pct="12")
        customer = customer_factory(state="GJ")
        dealer.customer_id = customer.id
        db.commit()
        quote = client.post("/api/v1/quotations", json=_order_body(), headers=as_dealer).json()
        assert quote["quote_no"].startswith("LGR/Q/")
        r = client.post(f"/api/v1/quotations/{quote['uid']}/convert",
                        headers={**as_dealer, **_idem()})
        assert r.status_code == 201
        assert r.json()["grand_total_paise"] == quote["grand_total_paise"]

    def test_double_convert_conflict(self, client, db, design_factory, customer_factory,
                                     dealer, as_dealer):
        design_factory("LGR-O1", rate_rupees="100")
        customer = customer_factory()
        dealer.customer_id = customer.id
        db.commit()
        quote = client.post("/api/v1/quotations", json=_order_body(), headers=as_dealer).json()
        client.post(f"/api/v1/quotations/{quote['uid']}/convert", headers={**as_dealer, **_idem()})
        r = client.post(f"/api/v1/quotations/{quote['uid']}/convert", headers={**as_dealer, **_idem()})
        assert r.status_code == 409


class TestCart:
    def test_cart_flow_and_summary_credit_strip(self, client, db, design_factory,
                                                customer_factory, dealer, as_dealer):
        design_factory("LGR-O1", rate_rupees="100", gst_pct="12")
        customer = customer_factory(state="GJ", credit_limit_rupees=100000)
        dealer.customer_id = customer.id
        db.commit()
        client.post("/api/v1/cart/items",
                    json={"design_no": "LGR-O1", "length_ft": "7", "breadth_ft": "4",
                          "quantity": 1},
                    headers=as_dealer)
        summary = client.get("/api/v1/cart/summary", headers=as_dealer).json()
        assert summary["grand_total_paise"] == 313600  # 2800 + 12% = 3136
        assert summary["credit"]["decision"] == "ALLOW"
        assert summary["credit"]["effective_limit_paise"] == 100000 * 100

    def test_unknown_design_rejected_at_add(self, client, db, dealer, as_dealer,
                                            customer_factory):
        customer = customer_factory()
        dealer.customer_id = customer.id
        db.commit()
        r = client.post("/api/v1/cart/items",
                        json={"design_no": "GHOST", "length_ft": "7", "breadth_ft": "4",
                              "quantity": 1},
                        headers=as_dealer)
        assert r.status_code == 404
