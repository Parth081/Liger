"""Analytics endpoints (API_SPEC §8). Sales reps see only their own customers."""
from __future__ import annotations

import uuid as uuid_mod
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.v1.deps import Actor, get_db, require
from app.core.exceptions import Forbidden, NotFound
from app.modules.analytics import service
from app.modules.analytics.service import Period
from app.modules.customers.models import Customer

router = APIRouter(prefix="/analytics", tags=["analytics"])
customer_router = APIRouter(prefix="/customers", tags=["analytics"])


def _rep_scope(actor: Actor) -> int | None:
    """BR-AC-04: a rep's reports cover only their own customers."""
    return actor.id if actor.role == "sales_rep" else None


def _period(from_: date | None, to: date | None) -> Period:
    end = to or date.today()
    start = from_ or (end.replace(day=1))
    return Period(start=start, end=end)


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db),
              actor: Actor = Depends(require("report.read"))):
    return service.dashboard(db, date.today(), rep_id=_rep_scope(actor))


@router.get("/sales")
def sales(group_by: str = Query("month", description="csv: month,customer,region,state,"
                                                     "distributor,rep,design,category"),
          from_: date | None = Query(None, alias="from"),
          to: date | None = Query(None),
          limit: int = Query(100, ge=1, le=500),
          db: Session = Depends(get_db),
          actor: Actor = Depends(require("report.read"))):
    groups = [g.strip() for g in group_by.split(",") if g.strip()]
    period = _period(from_, to)
    rows = service.sales_by(db, period, groups, rep_id=_rep_scope(actor), limit=limit)
    return {"period": {"from": str(period.start), "to": str(period.end)},
            "group_by": groups, "items": rows,
            "total_paise": sum(r["value_paise"] for r in rows)}


@router.get("/sales/drilldown")
def drilldown(from_: date | None = Query(None, alias="from"),
              to: date | None = Query(None),
              customer: str | None = Query(None),
              state: str | None = Query(None),
              month: str | None = Query(None),
              design: str | None = Query(None),
              category: str | None = Query(None),
              db: Session = Depends(get_db),
              actor: Actor = Depends(require("report.read"))):
    """BR-AN-04 — the orders behind any figure on any screen."""
    filters = {k: v for k, v in
               {"customer": customer, "state": state, "month": month,
                "design": design, "category": category}.items() if v}
    period = _period(from_, to)
    rows = service.drilldown(db, period, filters, rep_id=_rep_scope(actor))
    return {"filters": filters, "items": rows,
            "total_paise": sum(r["grand_total_paise"] for r in rows)}


@router.get("/outstanding")
def outstanding(db: Session = Depends(get_db),
                actor: Actor = Depends(require("report.read"))):
    rows = service.outstanding_report(db, date.today(), rep_id=_rep_scope(actor))
    return {"items": rows, "total_paise": sum(r["outstanding_paise"] for r in rows)}


@router.get("/collections")
def collections(from_: date | None = Query(None, alias="from"),
                to: date | None = Query(None),
                db: Session = Depends(get_db),
                actor: Actor = Depends(require("report.read"))):
    return service.collections_report(db, _period(from_, to))


@router.get("/top-customers")
def top_customers(limit: int = Query(10, ge=1, le=100),
                  from_: date | None = Query(None, alias="from"),
                  to: date | None = Query(None),
                  db: Session = Depends(get_db),
                  actor: Actor = Depends(require("report.read"))):
    return {"items": service.top_customers(db, _period(from_, to), limit=limit,
                                           rep_id=_rep_scope(actor))}


@router.get("/distributors")
def distributors(from_: date | None = Query(None, alias="from"),
                 to: date | None = Query(None),
                 db: Session = Depends(get_db),
                 actor: Actor = Depends(require("report.read"))):
    """BR-AN-03 roll-up: distributor totals across their sub-dealers."""
    return {"items": service.distributor_rollup(db, _period(from_, to))}


@router.get("/designs")
def designs(from_: date | None = Query(None, alias="from"),
            to: date | None = Query(None),
            limit: int = Query(20, ge=1, le=200),
            db: Session = Depends(get_db),
            actor: Actor = Depends(require("report.read"))):
    return {"items": service.design_performance(db, _period(from_, to), limit=limit)}


@router.get("/order-status")
def order_status(from_: date | None = Query(None, alias="from"),
                 to: date | None = Query(None),
                 db: Session = Depends(get_db),
                 actor: Actor = Depends(require("report.read"))):
    return {"items": service.order_status_mix(db, _period(from_, to))}


@router.get("/min-rule-impact")
def min_rule_impact(from_: date | None = Query(None, alias="from"),
                    to: date | None = Query(None),
                    db: Session = Depends(get_db),
                    actor: Actor = Depends(require("report.read"))):
    """What the 11 sq.ft minimum actually contributes (BR-SQFT-03)."""
    return service.min_rule_impact(db, _period(from_, to))


@customer_router.get("/{customer_uid}/360")
def customer_360(customer_uid: str, db: Session = Depends(get_db),
                 actor: Actor = Depends(require("report.read", "customer.read"))):
    customer = db.query(Customer).filter(
        Customer.uid == uuid_mod.UUID(customer_uid)).first()
    if customer is None:
        raise NotFound("Customer not found")
    if actor.role == "sales_rep" and customer.sales_rep_id != actor.id:
        raise Forbidden("This customer is not assigned to you")
    return service.customer_360(db, customer, date.today())
