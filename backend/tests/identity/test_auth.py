"""Auth flow integration tests (BR-AC-09): staff login, 2FA, OTP, refresh rotation."""
from app.core import security
from app.modules.identity.models import OtpRequest, RefreshToken


class TestStaffLogin:
    def test_login_ok(self, client, super_admin):
        r = client.post("/api/v1/auth/staff/login",
                        json={"email": "owner@ligertest.com", "password": "owner-pass-123"})
        assert r.status_code == 200
        body = r.json()
        assert body["requires_2fa"] is False
        assert body["access_token"] and body["refresh_token"]

    def test_wrong_password(self, client, super_admin):
        r = client.post("/api/v1/auth/staff/login",
                        json={"email": "owner@ligertest.com", "password": "nope"})
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "UNAUTHORIZED"

    def test_unknown_email_same_error(self, client, db):
        r = client.post("/api/v1/auth/staff/login",
                        json={"email": "ghost@ligertest.com", "password": "x"})
        assert r.status_code == 401

    def test_lockout_after_5_failures(self, client, super_admin):
        for _ in range(5):
            client.post("/api/v1/auth/staff/login",
                        json={"email": "owner@ligertest.com", "password": "wrong"})
        r = client.post("/api/v1/auth/staff/login",
                        json={"email": "owner@ligertest.com", "password": "owner-pass-123"})
        assert r.status_code == 429

    def test_2fa_challenge_flow(self, client, db, super_admin):
        import pyotp

        secret = security.new_totp_secret()
        super_admin.totp_secret = secret
        super_admin.is_2fa_enabled = True
        db.commit()

        r = client.post("/api/v1/auth/staff/login",
                        json={"email": "owner@ligertest.com", "password": "owner-pass-123"})
        assert r.status_code == 200
        body = r.json()
        assert body["requires_2fa"] is True and "access_token" not in body

        r2 = client.post("/api/v1/auth/staff/2fa",
                         json={"challenge_token": body["challenge_token"],
                               "code": pyotp.TOTP(secret).now()})
        assert r2.status_code == 200
        assert r2.json()["access_token"]

    def test_2fa_wrong_code(self, client, db, super_admin):
        super_admin.totp_secret = security.new_totp_secret()
        super_admin.is_2fa_enabled = True
        db.commit()
        body = client.post("/api/v1/auth/staff/login",
                           json={"email": "owner@ligertest.com", "password": "owner-pass-123"}).json()
        r = client.post("/api/v1/auth/staff/2fa",
                        json={"challenge_token": body["challenge_token"], "code": "000000"})
        assert r.status_code == 401


class TestDealerOTP:
    def test_request_and_verify(self, client, db, dealer):
        r = client.post("/api/v1/auth/otp/request", json={"phone": dealer.phone})
        assert r.status_code == 200
        code = r.json()["debug_code"]  # local env only

        r2 = client.post("/api/v1/auth/otp/verify", json={"phone": dealer.phone, "code": code})
        assert r2.status_code == 200
        token = r2.json()["access_token"]
        payload = security.decode_token(token)
        assert payload["customer_id"] == dealer.customer_id  # BR-AC-07

    def test_unknown_phone_no_enumeration(self, client, db):
        r = client.post("/api/v1/auth/otp/request", json={"phone": "+919999999999"})
        assert r.status_code == 200
        assert r.json() == {"sent": True}
        assert db.query(OtpRequest).count() == 0

    def test_BR_AC_09_rate_limit_5_per_hour(self, client, db, dealer):
        for _ in range(5):
            assert client.post("/api/v1/auth/otp/request", json={"phone": dealer.phone}).status_code == 200
        r = client.post("/api/v1/auth/otp/request", json={"phone": dealer.phone})
        assert r.status_code == 429

    def test_wrong_otp_then_attempt_cap(self, client, db, dealer):
        code = client.post("/api/v1/auth/otp/request", json={"phone": dealer.phone}).json()["debug_code"]
        for _ in range(5):
            r = client.post("/api/v1/auth/otp/verify", json={"phone": dealer.phone, "code": "000000"})
            assert r.status_code == 401
        # attempts exhausted — even the right code is now refused
        r = client.post("/api/v1/auth/otp/verify", json={"phone": dealer.phone, "code": code})
        assert r.status_code == 429

    def test_otp_single_use(self, client, db, dealer):
        code = client.post("/api/v1/auth/otp/request", json={"phone": dealer.phone}).json()["debug_code"]
        assert client.post("/api/v1/auth/otp/verify",
                           json={"phone": dealer.phone, "code": code}).status_code == 200
        r = client.post("/api/v1/auth/otp/verify", json={"phone": dealer.phone, "code": code})
        assert r.status_code == 401


class TestRefreshRotation:
    def _login(self, client):
        return client.post("/api/v1/auth/staff/login",
                           json={"email": "owner@ligertest.com", "password": "owner-pass-123"}).json()

    def test_rotation(self, client, db, super_admin):
        body = self._login(client)
        r = client.post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
        assert r.status_code == 200
        new = r.json()
        assert new["refresh_token"] != body["refresh_token"]
        # old token is now revoked
        r2 = client.post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
        assert r2.status_code == 401

    def test_logout_revokes(self, client, db, super_admin):
        body = self._login(client)
        assert client.post("/api/v1/auth/logout",
                           json={"refresh_token": body["refresh_token"]}).status_code == 200
        r = client.post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
        assert r.status_code == 401

    def test_only_hash_at_rest(self, client, db, super_admin):
        body = self._login(client)
        rows = db.query(RefreshToken).all()
        assert rows and all(row.token_hash != body["refresh_token"] for row in rows)


class TestMe:
    def test_staff_me(self, client, as_staff):
        r = client.get("/api/v1/auth/me", headers=as_staff("accounts"))
        assert r.status_code == 200
        body = r.json()
        assert body["role"] == "accounts"
        assert "payment.confirm_cash" in body["permissions"]
        assert "design.write" not in body["permissions"]  # BR-AC-03

    def test_dealer_me(self, client, as_dealer, dealer):
        r = client.get("/api/v1/auth/me", headers=as_dealer)
        assert r.status_code == 200
        assert r.json()["customer_id"] == dealer.customer_id

    def test_no_token(self, client):
        assert client.get("/api/v1/auth/me").status_code == 401

    def test_garbage_token(self, client):
        r = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer garbage"})
        assert r.status_code == 401
