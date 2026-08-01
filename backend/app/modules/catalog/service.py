"""Catalogue service — design CRUD, order-form lookup, CSV import (BR-CAT-*)."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.exceptions import Conflict, RateNotFound, ValidationFailed
from app.modules.catalog.models import Category, Design
from app.modules.pricing.rate_resolver import resolve_rate
from app.modules.pricing.service import get_design_ci


def create_design(db: Session, data: dict, actor_id: int) -> Design:
    existing = (
        db.query(Design)
        .filter(func.lower(Design.design_no) == data["design_no"].strip().lower())
        .first()
    )
    if existing is not None:
        raise Conflict(f"Design number '{data['design_no']}' already exists")  # BR-CAT-01
    category = db.get(Category, data["category_id"])
    if category is None:
        raise ValidationFailed("Unknown category", {"field": "category_id"})
    design = Design(**data, created_by=actor_id)
    db.add(design)
    write_audit(db, actor_type="user", actor_id=actor_id, action="design.create",
                entity_type="design", entity_id=data["design_no"])
    db.commit()
    db.refresh(design)
    return design


def update_design(db: Session, design: Design, changes: dict, actor_id: int) -> Design:
    before = {k: str(getattr(design, k)) for k in changes}
    for k, v in changes.items():
        setattr(design, k, v)
    design.updated_by = actor_id
    write_audit(db, actor_type="user", actor_id=actor_id, action="design.update",
                entity_type="design", entity_id=design.design_no,
                before=before, after={k: str(v) for k, v in changes.items()})
    db.commit()
    db.refresh(design)
    return design


def design_lookup(db: Session, design_no: str, on: date, customer_id: int | None) -> dict:
    """BR-CAT-06: the order-form lookup — design + images + resolved rate."""
    design = get_design_ci(db, design_no)
    payload: dict = {
        "uid": str(design.uid),
        "design_no": design.design_no,
        "name": design.name,
        "category": design.category.name,
        "product_type": design.category.product_type,
        "collection": design.collection,
        "colour": design.colour,
        "status": design.status,
        "hsn_code": design.hsn_code,
        "gst_pct": float(design.gst_pct),
        "images": [
            {"url": img.url, "variants": img.variants, "alt": img.alt_text}
            for img in design.images
        ],
    }
    try:
        rate = resolve_rate(db, design, on, customer_id)
        payload["rate_paise"] = rate.rate_paise
        payload["rate_source"] = rate.rate_source
    except RateNotFound:
        payload["rate_paise"] = None
        payload["rate_source"] = None
        payload["rate_error"] = "No rate configured for this design"
    return payload


def list_designs(db: Session, *, q: str | None, category_id: int | None,
                 status: str | None, cursor: int | None, limit: int) -> tuple[list[Design], str | None]:
    """R13: cursor pagination, never OFFSET."""
    query = db.query(Design).filter(Design.deleted_at.is_(None))
    if q:
        like = f"%{q.strip().lower()}%"
        query = query.filter(
            func.lower(Design.design_no).like(like) | func.lower(Design.name).like(like)
        )
    if category_id is not None:
        query = query.filter(Design.category_id == category_id)
    if status:
        query = query.filter(Design.status == status)
    if cursor is not None:
        query = query.filter(Design.id > cursor)
    rows = query.order_by(Design.id).limit(limit + 1).all()
    next_cursor = str(rows[limit - 1].id) if len(rows) > limit else None
    return rows[:limit], next_cursor


@dataclass
class ImportReport:
    total: int = 0
    ok: int = 0
    failed: int = 0
    errors: list[str] | None = None
    dry_run: bool = True


REQUIRED_COLUMNS = {"design_no", "name", "category_code", "rate_rupees", "gst_pct"}


def import_designs_csv(db: Session, content: str, *, dry_run: bool, actor_id: int) -> ImportReport:
    """BR-CAT-09: dry-run -> validation report -> commit. Re-runnable; existing
    design numbers are updated, not duplicated."""
    reader = csv.DictReader(io.StringIO(content))
    if reader.fieldnames is None or not REQUIRED_COLUMNS.issubset(set(reader.fieldnames)):
        raise ValidationFailed(
            f"CSV must have columns: {', '.join(sorted(REQUIRED_COLUMNS))} (optional: hsn_code, collection, colour)"
        )

    categories = {c.code: c for c in db.query(Category).all()}
    report = ImportReport(errors=[], dry_run=dry_run)

    for row_no, row in enumerate(reader, start=2):
        report.total += 1
        design_no = (row.get("design_no") or "").strip()
        name = (row.get("name") or "").strip()
        cat_code = (row.get("category_code") or "").strip().upper()
        problems: list[str] = []
        if not design_no:
            problems.append("design_no empty")
        if not name:
            problems.append("name empty")
        category = categories.get(cat_code)
        if category is None:
            problems.append(f"unknown category_code '{cat_code}'")
        rate_paise = 0
        try:
            # R1: rupees parsed as Decimal from text, converted to paise
            rate_paise = int((Decimal(row["rate_rupees"]) * 100).quantize(Decimal("1")))
            if rate_paise <= 0:
                problems.append("rate must be > 0")
        except (InvalidOperation, KeyError):
            problems.append(f"bad rate_rupees '{row.get('rate_rupees')}'")
        try:
            gst_pct = Decimal(row["gst_pct"])
            if gst_pct < 0 or gst_pct > 28:
                problems.append(f"gst_pct out of range '{gst_pct}'")
        except (InvalidOperation, KeyError):
            problems.append(f"bad gst_pct '{row.get('gst_pct')}'")
            gst_pct = Decimal("0")

        if problems:
            report.failed += 1
            assert report.errors is not None
            report.errors.append(f"row {row_no} ({design_no or '?'}): {'; '.join(problems)}")
            continue

        report.ok += 1
        if dry_run:
            continue

        assert category is not None
        existing = (
            db.query(Design)
            .filter(func.lower(Design.design_no) == design_no.lower())
            .first()
        )
        values = dict(
            name=name,
            category_id=category.id,
            base_rate_paise=rate_paise,
            gst_pct=gst_pct,
            hsn_code=(row.get("hsn_code") or "").strip() or None,
            collection=(row.get("collection") or "").strip() or None,
            colour=(row.get("colour") or "").strip() or None,
        )
        if existing is None:
            db.add(Design(design_no=design_no, created_by=actor_id, **values))
        else:
            for k, v in values.items():
                setattr(existing, k, v)
            existing.updated_by = actor_id

    if not dry_run:
        write_audit(db, actor_type="user", actor_id=actor_id, action="import.designs",
                    entity_type="import", entity_id="designs",
                    after={"total": report.total, "ok": report.ok, "failed": report.failed})
        db.commit()
    return report
