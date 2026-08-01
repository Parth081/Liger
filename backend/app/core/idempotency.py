"""Idempotency-Key replay store (R6).

Money-touching endpoints call `begin()` before doing work and `store()` after.
A repeated key with the same request hash returns the stored response; the
same key with a DIFFERENT payload is a 409 (client bug, never silently
create a second effect).
"""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.exceptions import Conflict
from app.db.base import utcnow
from app.modules.admin.models import IdempotencyKey

_TTL_HOURS = 48


def request_hash(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def begin(db: Session, key: str, endpoint: str, payload: Any) -> dict[str, Any] | None:
    """Returns the stored response for a replay, or None to proceed.

    Inserting the row up-front (before the work) means two concurrent requests
    with the same key race on the unique index — one wins, one replays/409s.
    """
    rhash = request_hash(payload)
    existing = db.query(IdempotencyKey).filter(IdempotencyKey.key == key).first()
    if existing is not None:
        if existing.request_hash != rhash:
            raise Conflict(
                "Idempotency-Key was already used with a different payload",
                {"key": key},
            )
        if existing.response is None:
            # First request still in flight (or crashed before storing).
            raise Conflict("Request with this Idempotency-Key is already in progress", {"key": key})
        return {"status_code": existing.status_code, "body": existing.response}

    db.add(IdempotencyKey(
        key=key,
        endpoint=endpoint,
        request_hash=rhash,
        response=None,
        status_code=None,
        expires_at=utcnow() + timedelta(hours=_TTL_HOURS),
    ))
    db.flush()
    return None


def store(db: Session, key: str, status_code: int, body: dict[str, Any]) -> None:
    row = db.query(IdempotencyKey).filter(IdempotencyKey.key == key).first()
    if row is not None:
        row.status_code = status_code
        row.response = body
        db.flush()


def purge_expired(db: Session) -> int:
    """Nightly maintenance task."""
    n = (
        db.query(IdempotencyKey)
        .filter(IdempotencyKey.expires_at < utcnow())
        .delete(synchronize_session=False)
    )
    db.commit()
    return n
