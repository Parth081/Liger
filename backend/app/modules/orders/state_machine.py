"""Order state machine — BR-ORD-01/02. Explicit whitelist enforced in the
service layer; the UI merely reflects it."""
from __future__ import annotations

from app.core.exceptions import InvalidTransition

# from_status -> {to_status: required_permission (None = internal/system)}
TRANSITIONS: dict[str, dict[str, str | None]] = {
    "DRAFT": {
        "PENDING_APPROVAL": None,
        "CONFIRMED": None,
        "CANCELLED": "order.cancel",
    },
    "PENDING_APPROVAL": {
        "CONFIRMED": "order.approve",       # BR-CR-13 / BR-PR-07
        "CANCELLED": "order.cancel",
    },
    "CONFIRMED": {
        "IN_PRODUCTION": "order.status.production",
        "ON_HOLD_CREDIT": None,             # system, via credit engine
        "CANCELLED": "order.cancel",        # BR-ORD-09: admin only, pre-production
    },
    "ON_HOLD_CREDIT": {
        "CONFIRMED": "order.approve",
        "CANCELLED": "order.cancel",
    },
    "IN_PRODUCTION": {
        "READY": "order.status.production",
        # BR-ORD-09: no cancellation once in production — credit note path in P4
    },
    "READY": {
        "DISPATCHED": "order.status.dispatch",
    },
    "DISPATCHED": {
        "DELIVERED": "order.status.dispatch",
        "PARTIALLY_DELIVERED": "order.status.dispatch",
    },
    "PARTIALLY_DELIVERED": {
        "DISPATCHED": "order.status.dispatch",
        "DELIVERED": "order.status.dispatch",
    },
    "DELIVERED": {
        "CLOSED": None,                     # system, on full payment (P4)
    },
    "CLOSED": {},
    "CANCELLED": {},
}


def required_permission(from_status: str, to_status: str) -> str | None:
    """Returns the permission needed, or raises InvalidTransition (BR-ORD-02)."""
    allowed = TRANSITIONS.get(from_status, {})
    if to_status not in allowed:
        raise InvalidTransition(
            f"Cannot move an order from {from_status} to {to_status}",
            {"from": from_status, "to": to_status,
             "allowed": sorted(allowed)},
        )
    return allowed[to_status]
