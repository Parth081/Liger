"""Security primitives: hashing, JWT, OTP hashing, TOTP."""
import pytest

from app.core import security


class TestPasswords:
    def test_hash_verify_roundtrip(self):
        h = security.hash_password("s3cret-pass")
        assert h != "s3cret-pass"
        assert security.verify_password("s3cret-pass", h)

    def test_wrong_password(self):
        h = security.hash_password("right")
        assert not security.verify_password("wrong", h)

    def test_garbage_hash(self):
        assert not security.verify_password("x", "not-a-hash")


class TestJWT:
    def test_roundtrip_staff(self):
        token = security.create_access_token("user", 42, "admin")
        payload = security.decode_token(token)
        assert payload["sub"] == "user:42"
        assert payload["type"] == "user"
        assert payload["role"] == "admin"
        assert "customer_id" not in payload

    def test_BR_AC_07_dealer_scope_in_token(self):
        token = security.create_access_token("customer_user", 7, "customer", customer_id=99)
        payload = security.decode_token(token)
        assert payload["customer_id"] == 99

    def test_tampered_token_rejected(self):
        token = security.create_access_token("user", 1, "admin")
        with pytest.raises(Exception):
            security.decode_token(token[:-2] + "xx")


class TestOTP:
    def test_generate_length_and_digits(self):
        code = security.generate_otp()
        assert len(code) == 6 and code.isdigit()

    def test_hash_verify(self):
        code = security.generate_otp()
        h = security.hash_otp(code, "+919876543210")
        assert security.verify_otp_hash(code, "+919876543210", h)

    def test_wrong_code(self):
        h = security.hash_otp("123456", "+919876543210")
        assert not security.verify_otp_hash("654321", "+919876543210", h)

    def test_phone_bound(self):
        """The same code for a different phone must not verify."""
        h = security.hash_otp("123456", "+919876543210")
        assert not security.verify_otp_hash("123456", "+919999999999", h)


class TestRefresh:
    def test_new_refresh_hash_stored_not_raw(self):
        raw, h = security.new_refresh_token()
        assert raw != h
        assert security.hash_opaque(raw) == h


class TestTOTP:
    def test_roundtrip(self):
        import pyotp

        secret = security.new_totp_secret()
        code = pyotp.TOTP(secret).now()
        assert security.verify_totp(secret, code)

    def test_wrong_code(self):
        secret = security.new_totp_secret()
        assert not security.verify_totp(secret, "000000")

    def test_provisioning_uri(self):
        secret = security.new_totp_secret()
        uri = security.totp_provisioning_uri(secret, "owner@liger.in")
        assert uri.startswith("otpauth://totp/") and "Liger" in uri
