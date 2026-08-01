"""Shared API dependencies: actor resolution, permission gate, pagination, idempotency."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, Query, Request
from sqlalchemy.orm import Session

from app.core import security
from app.core.exceptions import Forbidden, Unauthorized, ValidationFailed
from app.core.permissions import role_has
from app.db.session import get_db
from app.modules.identity.models import CustomerUser, User

__all__ = ["get_db", "Actor", "get_actor", "require", "Pagination", "pagination", "idempotency_key"]


@dataclass
class Actor:
    """The authenticated caller. For dealers, customer_id is the ONLY scope
    trusted anywhere (BR-AC-07) — never a request parameter."""

    type: str                      # user | customer_user
    id: int
    role: str                      # staff role code, or "customer"
    customer_id: int | None = None
    user: User | None = None
    customer_user: CustomerUser | None = None

    @property
    def is_dealer(self) -> bool:
        return self.type == "customer_user"


def get_actor(request: Request, db: Session = Depends(get_db)) -> Actor:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise Unauthorized("Missing bearer token")
    try:
        payload = security.decode_token(auth[7:])
    except Exception:
        raise Unauthorized("Invalid or expired token") from None

    subject_type = payload.get("type")
    if subject_type == "user":
        user = db.get(User, int(payload["sub"].split(":")[1]))
        if user is None or not user.is_active:
            raise Unauthorized("Account disabled")
        return Actor(type="user", id=user.id, role=user.role.code, user=user)
    if subject_type == "customer_user":
        cu = db.get(CustomerUser, int(payload["sub"].split(":")[1]))
        if cu is None or not cu.is_active:
            raise Unauthorized("Account disabled")
        return Actor(type="customer_user", id=cu.id, role="customer",
                     customer_id=cu.customer_id, customer_user=cu)
    raise Unauthorized("Invalid token type")


def require(*permissions: str):
    """Server-side permission gate (R11). Dealers pass only where a route
    explicitly allows role 'customer'."""

    def dependency(actor: Actor = Depends(get_actor)) -> Actor:
        if actor.is_dealer:
            if "customer" in permissions:
                return actor
            raise Forbidden("Not available to dealer accounts")
        for perm in permissions:
            if perm == "customer":
                continue
            if role_has(actor.role, perm):
                return actor
        raise Forbidden("You do not have permission for this action")

    return dependency


@dataclass
class Pagination:
    limit: int
    cursor: int | None  # opaque to clients; internally the last-seen id (R13 — no OFFSET)


def pagination(
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None),
) -> Pagination:
    cur: int | None = None
    if cursor:
        try:
            cur = int(cursor)
        except ValueError:
            raise ValidationFailed("Invalid cursor") from None
    return Pagination(limit=limit, cursor=cur)


def idempotency_key(idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=80)) -> str:
    """R6: required on money-touching mutations."""
    return idempotency_key
