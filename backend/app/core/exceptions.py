"""Domain exception hierarchy, mapped to the API_SPEC error envelope in one place.

Services raise these; they never raise HTTPException (CLAUDE.md §8).
"""
from typing import Any


class DomainError(Exception):
    """Base for every business error. `code` matches API_SPEC error codes."""

    code = "INTERNAL_ERROR"
    http_status = 500

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ValidationFailed(DomainError):
    code = "VALIDATION_ERROR"
    http_status = 400


class Unauthorized(DomainError):
    code = "UNAUTHORIZED"
    http_status = 401


class Forbidden(DomainError):
    code = "FORBIDDEN"
    http_status = 403


class NotFound(DomainError):
    code = "NOT_FOUND"
    http_status = 404


class Conflict(DomainError):
    code = "CONFLICT"
    http_status = 409


class RateLimited(DomainError):
    code = "RATE_LIMITED"
    http_status = 429


class DesignNotFound(NotFound):
    code = "DESIGN_NOT_FOUND"


class RateNotFound(DomainError):
    """BR-PR-01(3): a line that cannot be priced is rejected, never zero-rated."""

    code = "RATE_NOT_FOUND"
    http_status = 422


class CreditBlocked(Forbidden):
    """BR-CR-10/11: hard block. `details` carries outstanding + overdue invoices."""

    code = "CREDIT_BLOCKED"


class CreditLimitExceeded(Forbidden):
    """BR-CR-13."""

    code = "CREDIT_LIMIT_EXCEEDED"


class InvalidTransition(DomainError):
    """BR-ORD-02: order state machine whitelist violation."""

    code = "INVALID_TRANSITION"
    http_status = 409


class PaymentFailed(DomainError):
    code = "PAYMENT_FAILED"
    http_status = 402
