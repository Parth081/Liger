"""Hashing, JWT, TOTP and OTP primitives (BR-AC-09, ARCHITECTURE §6)."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.core.config import get_config

_hasher = PasswordHasher()


# ---------------- passwords (argon2) ----------------
def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, plain)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


# ---------------- JWT ----------------
def create_access_token(subject_type: str, subject_id: int, role: str,
                        customer_id: int | None = None,
                        extra: dict[str, Any] | None = None) -> str:
    cfg = get_config()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": f"{subject_type}:{subject_id}",
        "type": subject_type,           # user | customer_user
        "role": role,                   # staff role code, or "customer"
        "iat": now,
        "exp": now + timedelta(minutes=cfg.access_token_minutes),
    }
    if customer_id is not None:
        # BR-AC-07: dealer scope travels in the token, never in request params.
        payload["customer_id"] = customer_id
    if extra:
        payload.update(extra)
    return jwt.encode(payload, cfg.jwt_secret, algorithm=cfg.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    cfg = get_config()
    return jwt.decode(token, cfg.jwt_secret, algorithms=[cfg.jwt_algorithm])


# ---------------- refresh tokens (opaque, hashed at rest) ----------------
def new_refresh_token() -> tuple[str, str]:
    """Returns (raw_token, sha256_hash). Only the hash is stored."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_opaque(raw)


def hash_opaque(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------- OTP ----------------
def generate_otp(length: int | None = None) -> str:
    cfg = get_config()
    n = length or cfg.otp_length
    return "".join(secrets.choice("0123456789") for _ in range(n))


def hash_otp(code: str, phone: str) -> str:
    """HMAC so a leaked table cannot be brute-forced offline per-row trivially."""
    cfg = get_config()
    return hmac.new(cfg.jwt_secret.encode(), f"{phone}:{code}".encode(), hashlib.sha256).hexdigest()


def verify_otp_hash(code: str, phone: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_otp(code, phone), stored_hash)


# ---------------- TOTP 2FA ----------------
def new_totp_secret() -> str:
    return pyotp.random_base32()


def verify_totp(secret: str, code: str) -> bool:
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def totp_provisioning_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="Liger")
