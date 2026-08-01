"""Catalogue API tests: lookup, CRUD permissions, import (BR-CAT-*)."""


class TestDesignLookup:
    def test_BR_CAT_06_lookup_returns_rate(self, client, db, design_factory, as_dealer):
        design_factory("LGR-9001", rate_rupees="150")
        r = client.get("/api/v1/designs/LGR-9001", headers=as_dealer)
        assert r.status_code == 200
        body = r.json()
        assert body["rate_paise"] == 15000
        assert body["name"] == "Design LGR-9001"

    def test_BR_CAT_01_case_insensitive(self, client, db, design_factory, as_dealer):
        design_factory("LGR-9002")
        assert client.get("/api/v1/designs/lgr-9002", headers=as_dealer).status_code == 200

    def test_BR_CAT_07_not_found_explicit(self, client, db, as_dealer):
        r = client.get("/api/v1/designs/NOPE-1", headers=as_dealer)
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "DESIGN_NOT_FOUND"

    def test_lookup_without_rate_flags_error(self, client, db, design_factory, as_dealer):
        design_factory("LGR-9003", rate_rupees="0")
        body = client.get("/api/v1/designs/LGR-9003", headers=as_dealer).json()
        assert body["rate_paise"] is None and "rate_error" in body


class TestDesignPermissions:
    def _payload(self, no="LGR-9100"):
        cat_id = 1
        return {"design_no": no, "name": "New Design", "category_id": cat_id,
                "base_rate_paise": 10000}

    def test_R11_dealer_cannot_create(self, client, db, as_dealer):
        r = client.post("/api/v1/designs", json=self._payload(), headers=as_dealer)
        assert r.status_code == 403

    def test_R11_accounts_cannot_create(self, client, db, as_staff):
        r = client.post("/api/v1/designs", json=self._payload(), headers=as_staff("accounts"))
        assert r.status_code == 403  # BR-AC-03

    def test_admin_can_create(self, client, db, as_staff):
        from app.modules.catalog.models import Category

        cat = db.query(Category).first()
        payload = self._payload()
        payload["category_id"] = cat.id
        r = client.post("/api/v1/designs", json=payload, headers=as_staff("admin"))
        assert r.status_code == 201

    def test_duplicate_design_no_conflict(self, client, db, design_factory, as_staff):
        from app.modules.catalog.models import Category

        design_factory("LGR-9101")
        cat = db.query(Category).first()
        r = client.post("/api/v1/designs",
                        json={"design_no": "lgr-9101", "name": "Dup", "category_id": cat.id},
                        headers=as_staff("admin"))
        assert r.status_code == 409  # case-insensitive uniqueness (BR-CAT-01)


class TestImport:
    CSV = (
        "design_no,name,category_code,rate_rupees,gst_pct,hsn_code\n"
        "LGR-A1,Aurora Ivory,ZEBRA,125.50,12,5407\n"
        "LGR-A2,Aurora Grey,ZEBRA,125.50,12,5407\n"
        "LGR-BAD,,ZEBRA,not-a-number,99,\n"
    )

    def test_BR_CAT_09_dry_run_reports_without_writing(self, client, db, as_staff):
        from app.modules.catalog.models import Design

        r = client.post("/api/v1/imports/designs?dry_run=true",
                        content=self.CSV, headers={**as_staff("super_admin"),
                                                   "Content-Type": "text/csv"})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 3 and body["ok"] == 2 and body["failed"] == 1
        assert any("LGR-BAD" in e for e in body["errors"])
        assert db.query(Design).count() == 0  # dry run wrote nothing

    def test_commit_then_rerun_updates_not_duplicates(self, client, db, as_staff):
        from app.modules.catalog.models import Design

        headers = {**as_staff("super_admin"), "Content-Type": "text/csv"}
        client.post("/api/v1/imports/designs?dry_run=false", content=self.CSV, headers=headers)
        assert db.query(Design).count() == 2
        client.post("/api/v1/imports/designs?dry_run=false", content=self.CSV, headers=headers)
        assert db.query(Design).count() == 2  # re-runnable, no duplicates

    def test_R11_admin_cannot_import(self, client, db, as_staff):
        r = client.post("/api/v1/imports/designs", content=self.CSV,
                        headers={**as_staff("admin"), "Content-Type": "text/csv"})
        assert r.status_code == 403  # import.run is super_admin only
