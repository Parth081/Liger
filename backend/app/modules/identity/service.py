"""Auth flows: staff login + 2FA, dealer OTP, rotating refresh (BR-AC-09)."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import security
from app.core.audit import write_audit
from app.core.config import get_config
from app.core.exceptions import RateLimited, Unauthorized
from app.db.base import as_utc, utcnow
from app.modules.identity.models import CustomerUser, OtpRequest, RefreshToken, User

_LOCKOUT_MINUTES = 15
_MAX_FAILED_LOGINS = 5


# ---------------- staff ----------------
def staff_login(db: Session, email: str, password: str, ip: str | None = None) -> dict:
    """Step 1. Returns tokens, or a 2FA challenge if enabled."""
    user = db.query(User).filter(func.lower(User.email) == email.lower(), User.is_active.is_(True)).first()
    if user is None:
        raise Unauthorized("Incorrect email or password")
    locked_until = as_utc(user.locked_until)
    if locked_until and locked_until > utcnow():
        raise RateLimited("Account temporarily locked. Try again later.")
    if not security.verify_password(password, user.password_hash):
        user.failed_login_count += 1
        if user.failed_login_count >= _MAX_FAILED_LOGINS:
            user.locked_until = utcnow() + timedelta(minutes=_LOCKOUT_MINUTES)
            user.failed_login_count = 0
        db.commit()
        raise Unauthorized("Incorrect email or password")

    user.failed_login_count = 0
    if user.is_2fa_enabled:
        db.commit()
        # Short-lived challenge token; exchanged at /auth/staff/2fa.
        challenge = security.create_access_token("2fa_challenge", user.id, user.role.code)
        return {"requires_2fa": True, "challenge_token": challenge}

    return _issue_staff_tokens(db, user, ip)


def staff_verify_2fa(db: Session, challenge_token: str, totp_code: str, ip: str | None = None) -> dict:
    try:
        payload = security.decode_token(challenge_token)
    except Exception:
        raise Unauthorized("Invalid or expired 2FA challenge") from None
    if payload.get("type") != "2fa_challenge":
        raise Unauthorized("Invalid 2FA challenge")
    user = db.get(User, int(payload["sub"].split(":")[1]))
    if user is None or not user.is_active or not user.totp_secret:
        raise Unauthorized("Invalid 2FA challenge")
    if not security.verify_totp(user.totp_secret, totp_code):
        raise Unauthorized("Incorrect 2FA code")
    return _issue_staff_tokens(db, user, ip)


def _issue_staff_tokens(db: Session, user: User, ip: str | None) -> dict:
    user.last_login_at = utcnow()
    access = security.create_access_token("user", user.id, user.role.code)
    refresh = _issue_refresh(db, "user", user.id, ip)
    write_audit(db, actor_type="user", actor_id=user.id, action="auth.login",
                entity_type="user", entity_id=user.id, ip=ip)
    db.commit()
    return {"requires_2fa": False, "access_token": access, "refresh_token": refresh, "token_type": "bearer"}


# ---------------- dealer OTP (BR-AC-09) ----------------
def request_otp(db: Session, phone: str, ip: str | None = None) -> dict:
    cfg = get_config()
    cu = db.query(CustomerUser).filter(CustomerUser.phone == phone, CustomerUser.is_active.is_(True)).first()
    if cu is None:
        # Deliberately identical response for unknown numbers — no enumeration.
        return {"sent": True}

    hour_ago = utcnow() - timedelta(hours=1)
    recent = (
        db.query(func.count(OtpRequest.id))
        .filter(OtpRequest.phone == phone, OtpRequest.created_at >= hour_ago)
        .scalar()
    )
    if recent >= cfg.otp_max_per_hour:
        raise RateLimited("Too many OTP requests. Please try again after an hour.")

    code = security.generate_otp()
    db.add(OtpRequest(
        phone=phone,
        code_hash=security.hash_otp(code, phone),
        purpose="login",
        expires_at=utcnow() + timedelta(minutes=cfg.otp_ttl_minutes),
        ip=ip,
    ))
    db.commit()
    # P5 wires this into the SMS/WhatsApp provider; until then local envs log it.
    result: dict = {"sent": True}
    if get_config().env == "local":
        result["debug_code"] = code
    return result


def verify_otp(db: Session, phone: str, code: str, ip: str | None = None) -> dict:
    cfg = get_config()
    req = (
        db.query(OtpRequest)
        .filter(
            OtpRequest.phone == phone,
            OtpRequest.consumed_at.is_(None),
            OtpRequest.expires_at > utcnow(),
        )
        .order_by(OtpRequest.created_at.desc())
        .first()
    )
    if req is None:
        raise Unauthorized("OTP expired or not requested")
    if req.attempts >= cfg.otp_max_attempts:
        raise RateLimited("Too many wrong attempts. Request a new OTP.")
    if not security.verify_otp_hash(code, phone, req.code_hash):
        req.attempts += 1
        db.commit()
        raise Unauthorized("Incorrect OTP")

    req.consumed_at = utcnow()
    cu = db.query(CustomerUser).filter(CustomerUser.phone == phone, CustomerUser.is_active.is_(True)).first()
    if cu is None:
        raise Unauthorized("Account not found or inactive")
    cu.last_login_at = utcnow()
    access = security.create_access_token("customer_user", cu.id, "customer", customer_id=cu.customer_id)
    refresh = _issue_refresh(db, "customer_user", cu.id, ip)
    write_audit(db, actor_type="customer_user", actor_id=cu.id, action="auth.otp_login",
                entity_type="customer_user", entity_id=cu.id, ip=ip)
    db.commit()
    return {"access_token": access, "refresh_token": refresh, "token_type": "bearer"}


# ---------------- refresh rotation ----------------
def _issue_refresh(db: Session, subject_type: str, subject_id: int, ip: str | None) -> str:
    cfg = get_config()
    raw, token_hash = security.new_refresh_token()
    db.add(RefreshToken(
        subject_type=subject_type,
        subject_id=subject_id,
        token_hash=token_hash,
        ip=ip,
        expires_at=utcnow() + timedelta(days=cfg.refresh_token_days),
    ))
    return raw


def refresh_tokens(db: Session, raw_refresh: str, ip: str | None = None) -> dict:
    token_hash = security.hash_opaque(raw_refresh)
    row = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
    expires_at = as_utc(row.expires_at) if row else None
    if row is None or row.revoked_at is not None or (expires_at and expires_at <= utcnow()):
        raise Unauthorized("Invalid refresh token")

    # Rotate: revoke old, issue new.
    row.revoked_at = utcnow()
    if row.subject_type == "user":
        user = db.get(User, row.subject_id)
        if user is None or not user.is_active:
            raise Unauthorized("Account disabled")
        access = security.create_access_token("user", user.id, user.role.code)
    else:
        cu = db.get(CustomerUser, row.subject_id)
        if cu is None or not cu.is_active:
            raise Unauthorized("Account disabled")
        access = security.create_access_token("customer_user", cu.id, "customer", customer_id=cu.customer_id)

    new_refresh = _issue_refresh(db, row.subject_type, row.subject_id, ip)
    db.commit()
    return {"access_token": access, "refresh_token": new_refresh, "token_type": "bearer"}


def logout(db: Session, raw_refresh: str) -> None:
    token_hash = security.hash_opaque(raw_refresh)
    row = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
    if row is not None and row.revoked_at is None:
        row.revoked_at = utcnow()
        db.commit()
