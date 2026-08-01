"""Catalogue endpoints (API_SPEC §2)."""
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.deps import Actor, Pagination, get_actor, get_db, pagination, require
from app.modules.catalog import service
from app.modules.catalog.models import Category
from app.modules.pricing.service import get_design_ci

router = APIRouter(tags=["catalogue"])


class DesignCreateIn(BaseModel):
    design_no: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=200)
    category_id: int
    collection: str | None = None
    colour: str | None = None
    composition: str | None = None
    width_of_goods: str | None = None
    hsn_code: str | None = None
    gst_pct: Decimal = Field(default=Decimal("12"), ge=0, le=28)
    base_rate_paise: int = Field(default=0, ge=0)


class DesignPatchIn(BaseModel):
    name: str | None = None
    collection: str | None = None
    colour: str | None = None
    hsn_code: str | None = None
    gst_pct: Decimal | None = Field(default=None, ge=0, le=28)
    base_rate_paise: int | None = Field(default=None, ge=0)
    status: str | None = Field(default=None, pattern="^(active|discontinued|out_of_stock)$")


@router.get("/designs")
def list_designs(
    q: str | None = Query(None, max_length=100),
    category_id: int | None = Query(None),
    status: str | None = Query(None, pattern="^(active|discontinued|out_of_stock)$"),
    page: Pagination = Depends(pagination),
    db: Session = Depends(get_db),
    actor: Actor = Depends(get_actor),
):
    rows, next_cursor = service.list_designs(
        db, q=q, category_id=category_id, status=status, cursor=page.cursor, limit=page.limit
    )
    return {
        "items": [
            {
                "uid": str(d.uid), "design_no": d.design_no, "name": d.name,
                "category": d.category.name, "status": d.status,
                "gst_pct": float(d.gst_pct),
                "cover_image": d.images[0].url if d.images else None,
            }
            for d in rows
        ],
        "next_cursor": next_cursor,
    }


@router.get("/designs/{design_no}")
def get_design(design_no: str, db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
    """BR-CAT-06: order-form lookup with the caller's resolved rate."""
    return service.design_lookup(db, design_no, date.today(), actor.customer_id)


@router.post("/designs", status_code=201)
def create_design(
    body: DesignCreateIn,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require("design.write")),
):
    design = service.create_design(db, body.model_dump(), actor.id)
    return {"uid": str(design.uid), "design_no": design.design_no}


@router.patch("/designs/{design_no}")
def patch_design(
    design_no: str,
    body: DesignPatchIn,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require("design.write")),
):
    design = get_design_ci(db, design_no)
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    service.update_design(db, design, changes, actor.id)
    return {"uid": str(design.uid), "design_no": design.design_no, "updated": sorted(changes)}


@router.get("/categories")
def list_categories(db: Session = Depends(get_db), actor: Actor = Depends(get_actor)):
    rows = db.query(Category).filter(Category.is_active.is_(True)).order_by(Category.sort_order).all()
    return {"items": [{"id": c.id, "name": c.name, "code": c.code, "product_type": c.product_type} for c in rows]}


@router.post("/imports/designs")
def import_designs(
    dry_run: bool = Query(True),
    csv_content: str = Body(..., media_type="text/csv"),
    db: Session = Depends(get_db),
    actor: Actor = Depends(require("import.run")),
):
    """BR-CAT-09: always dry-run first; the UI enforces the two-step flow."""
    report = service.import_designs_csv(db, csv_content, dry_run=dry_run, actor_id=actor.id)
    return {
        "dry_run": report.dry_run, "total": report.total,
        "ok": report.ok, "failed": report.failed, "errors": report.errors,
    }
