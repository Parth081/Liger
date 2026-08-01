"""Single audit writer (P0-T2-07). Every privileged mutation calls write_audit."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.modules.admin.models import AuditLog


def write_audit(
    db: Session,
    *,
    actor_type: str,
    actor_id: int | None,
    action: str,
    entity_type: str,
    entity_id: str | int,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Append an audit row inside the caller's transaction (BR-AC-08).

    Committed together with the change it describes — an audit entry for a
    rolled-back change must roll back too.
    """
    db.add(AuditLog(
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        before=before,
        after=after,
        ip=ip,
        user_agent=user_agent,
        created_at=utcnow(),
    ))
