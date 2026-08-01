"""Liger API entrypoint.

Routers are thin; business logic lives in modules/*/service.py (CLAUDE.md §8).
Domain exceptions are mapped to the API_SPEC error envelope here, once.
"""
import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.routers import (
    admin,
    analytics,
    auth,
    credit,
    designs,
    fulfilment,
    health,
    notifications,
    orders,
    payments,
    pricing,
)
from app.core.config import get_config
from app.core.exceptions import DomainError

logger = logging.getLogger("liger")


def create_app() -> FastAPI:
    from app.modules.notifications.hooks import register_handlers

    register_handlers()
    cfg = get_config()
    app = FastAPI(
        title="Liger API",
        version="1.0.0",
        docs_url="/docs" if cfg.env != "production" else None,
        redoc_url=None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*", "Idempotency-Key"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = f"req_{uuid.uuid4().hex[:20]}"
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        # Security headers (ARCHITECTURE §6)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError):
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "request_id": getattr(request.state, "request_id", None),
            }},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=400,
            content={"error": {
                "code": "VALIDATION_ERROR",
                "message": "Invalid request",
                "details": {"errors": exc.errors()},
                "request_id": getattr(request.state, "request_id", None),
            }},
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        # Never leak stack traces (API_SPEC §9.7)
        logger.exception("Unhandled error", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={"error": {
                "code": "INTERNAL_ERROR",
                "message": "Something went wrong. The team has been notified.",
                "details": {},
                "request_id": getattr(request.state, "request_id", None),
            }},
        )

    prefix = "/api/v1"
    app.include_router(health.router, prefix=prefix)
    app.include_router(auth.router, prefix=prefix)
    app.include_router(designs.router, prefix=prefix)
    app.include_router(pricing.router, prefix=prefix)
    app.include_router(orders.router, prefix=prefix)
    app.include_router(credit.router, prefix=prefix)
    app.include_router(credit.customer_router, prefix=prefix)
    app.include_router(payments.router, prefix=prefix)
    app.include_router(notifications.router, prefix=prefix)
    app.include_router(fulfilment.router, prefix=prefix)
    app.include_router(analytics.router, prefix=prefix)
    app.include_router(analytics.customer_router, prefix=prefix)
    app.include_router(admin.router, prefix=prefix)
    return app


app = create_app()
