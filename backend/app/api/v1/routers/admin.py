"""Admin endpoints: settings (R8), audit log, imports, reconciliation (API_SPEC §8)."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.deps import Actor, Pagination, get_db, pagination, require
from app.core import settings_registry
from app.core.settings_registry import SEED
from app.modules.admin import importers
from app.modules.admin.models import AuditLog, Setting, SettingHistory

router = APIRouter(tags=["admin"])

_IMPORTERS = {
    "customers": importers.import_customers,
    "opening_balances": importers.import_opening_balances,
    "open_invoices": importers.import_open_invoices,
    "order_history": importers.import_order_history,
}


# ---------------- settings (R8) ----------------
@router.get("/settings")
def list_settings(group: str | None = Query(None), db: Session = Depends(get_db),
                  actor: Actor = Depends(require("settings.write"))):
    q = db.query(Setting)
    if group:
        q = q.filter(Setting.group == group)
    rows = q.order_by(Setting.group, Setting.key).all()
    return {"items": [
        {"key": s.key, "value": s.value, "type": s.value_type, "group": s.group,
         "description": s.description}
        for s in rows
    ]}


class SettingIn(BaseModel):
    value: str = Field(min_length=1, max_length=255)


@router.patch("/settings/{key}")
def update_setting(key: str, body: SettingIn, db: Session = Depends(get_db),
                   actor: Actor = Depends(require("settings.write"))):
    """Every business rule value changes here — audited, no deploy (R8).

    BR-CR-40: flipping credit_enforcement_mode to 'enforce' is the owner's
    P9 go-live decision. It is logged loudly.
    """
    if key not in SEED:
        from app.core.exceptions import NotFound

        raise NotFound(f"Unknown setting '{key}'")
    settings_registry.set_value(db, key, body.value, actor_id=actor.id)
    return {"key": key, "value": body.value}


@router.get("/settings/history")
def settings_history(key: str | None = Query(None), db: Session = Depends(get_db),
                     actor: Actor = Depends(require("settings.write"))):
    q = db.query(SettingHistory)
    if key:
        q = q.filter(SettingHistory.key == key)
    rows = q.order_by(SettingHistory.id.desc()).limit(200).all()
    return {"items": [
        {"key": h.key, "old_value": h.old_value, "new_value": h.new_value,
         "changed_by": h.changed_by, "changed_at": h.changed_at.isoformat()}
        for h in rows
    ]}


# ---------------- audit log ----------------
@router.get("/audit-log")
def audit_log(entity_type: str | None = Query(None), entity_id: str | None = Query(None),
              action: str | None = Query(None), page: Pagination = Depends(pagination),
              db: Session = Depends(get_db),
              actor: Actor = Depends(require("audit.read"))):
    q = db.query(AuditLog)
    if entity_type:
        q = q.filter(AuditLog.entity_type == entity_type)
    if entity_id:
        q = q.filter(AuditLog.entity_id == entity_id)
    if action:
        q = q.filter(AuditLog.action == action)
    if page.cursor is not None:
        q = q.filter(AuditLog.id < page.cursor)
    rows = q.order_by(AuditLog.id.desc()).limit(page.limit + 1).all()
    next_cursor = str(rows[page.limit - 1].id) if len(rows) > page.limit else None
    return {
        "items": [
            {"action": a.action, "entity_type": a.entity_type, "entity_id": a.entity_id,
             "actor_type": a.actor_type, "actor_id": a.actor_id,
             "before": a.before, "after": a.after, "ip": a.ip,
             "at": a.created_at.isoformat()}
            for a in rows[: page.limit]
        ],
        "next_cursor": next_cursor,
    }


# ---------------- imports (P8-T2) ----------------
@router.post("/imports/{kind}")
def run_import(kind: str, dry_run: bool = Query(True),
               csv_content: str = Body(..., media_type="text/csv"),
               db: Session = Depends(get_db),
               actor: Actor = Depends(require("import.run"))):
    """Always dry-run first (P8-T2-08). Every importer is re-runnable."""
    if kind not in _IMPORTERS:
        from app.core.exceptions import NotFound

        raise NotFound(f"Unknown import '{kind}'",
                       {"available": sorted(_IMPORTERS)})
    report = _IMPORTERS[kind](db, csv_content, dry_run=dry_run, actor_id=actor.id)
    return report.as_dict()


@router.post("/imports/reconcile")
def reconcile(csv_content: str = Body(..., media_type="text/csv"),
              db: Session = Depends(get_db),
              actor: Actor = Depends(require("import.run"))):
    """P8-T2-09: system vs. the owner's books, per customer. Read-only.

    `clean: true` is the gate for retiring the offline books.
    """
    return importers.reconcile(db, csv_content)


@router.get("/imports/summary")
def import_summary(db: Session = Depends(get_db),
                   actor: Actor = Depends(require("import.run"))):
    return importers.import_summary(db)
