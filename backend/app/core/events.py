"""Domain event dispatch (ARCHITECTURE §2, R10).

Handlers are registered per event name. In production the registered handlers
enqueue Celery tasks (P5 wires notifications); in tests, `captured` collects
events for assertion. Dispatch is post-commit by convention: services call
emit() only after their transaction has committed.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("liger.events")

_handlers: dict[str, list[Callable[[dict[str, Any]], None]]] = {}
captured: list[tuple[str, dict[str, Any]]] = []
capture_mode = False


def on(event: str, handler: Callable[[dict[str, Any]], None]) -> None:
    _handlers.setdefault(event, []).append(handler)


def emit(event: str, payload: dict[str, Any]) -> None:
    if capture_mode:
        captured.append((event, payload))
    for handler in _handlers.get(event, []):
        try:
            handler(payload)
        except Exception:  # a failing side effect must never break the request (R10)
            logger.exception("Event handler failed for %s", event)


def reset() -> None:
    """Test helper."""
    captured.clear()
