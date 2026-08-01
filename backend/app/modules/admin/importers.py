"""Data migration importers — BR-LED-05, P8-T2.

Every importer follows the same contract (P8-T2-08):
    dry_run=True  -> validate, report, write nothing
    dry_run=False -> import, and be safe to re-run without duplicating

The reconciliation report (P8-T2-09) is what earns the right to switch off the
offline books: system outstanding vs. the owner's book figure, per customer,
with every variance listed.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.exceptions import ValidationFailed
from app.core.money import Money
from app.modules.credit import ledger
from app.modules.credit.models import Invoice
from app.modules.customers.models import Customer, Region
from app.modules.identity.models import CustomerUser
from app.modules.orders.models import Order, OrderItem


@dataclass
class ImportReport:
    kind: str
    dry_run: bool
    total: int = 0
    created: int = 0
    updated: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def error(self, row_no: int, ref: str, message: str) -> None:
        self.failed += 1
        self.errors.append(f"row {row_no} ({ref or '?'}): {message}")

    def as_dict(self) -> dict:
        return {"kind": self.kind, "dry_run": self.dry_run, "total": self.total,
                "created": self.created, "updated": self.updated, "failed": self.failed,
                "errors": self.errors[:200]}


def _rupees_to_paise(value: str) -> int:
    """R1: parse rupee text as Decimal — a float would lose paise."""
    cleaned = (value or "").replace(",", "").replace("₹", "").strip()
    if not cleaned:
        return 0
    return int((Decimal(cleaned) * 100).quantize(Decimal("1")))


def _reader(content: str, required: set[str], kind: str) -> csv.DictReader:
    reader = csv.DictReader(io.StringIO(content))
    if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
        raise ValidationFailed(
            f"{kind} CSV needs columns: {', '.join(sorted(required))}",
            {"found": reader.fieldnames or []},
        )
    return reader


# ---------------- customers (P8-T2-01) ----------------
CUSTOMER_COLUMNS = {"code", "business_name", "phone"}


def import_customers(db: Session, content: str, *, dry_run: bool,
                     actor_id: int) -> ImportReport:
    """Optional columns: gstin, state, city, pincode, region, distributor_code,
    credit_limit_rupees, credit_days, language, email."""
    report = ImportReport(kind="customers", dry_run=dry_run)
    reader = _reader(content, CUSTOMER_COLUMNS, "Customer")
    regions = {r.name.lower(): r for r in db.query(Region).all()}
    seen_codes: set[str] = set()

    pending_distributors: list[tuple[str, str]] = []
    for row_no, row in enumerate(reader, start=2):
        report.total += 1
        code = (row.get("code") or "").strip()
        name = (row.get("business_name") or "").strip()
        phone = (row.get("phone") or "").strip()
        problems = []
        if not code:
            problems.append("code empty")
        if not name:
            problems.append("business_name empty")
        if not phone or not phone.replace("+", "").isdigit():
            problems.append(f"phone invalid '{phone}'")
        if code in seen_codes:
            problems.append("duplicate code in file")
        credit_limit = 0
        try:
            credit_limit = _rupees_to_paise(row.get("credit_limit_rupees", "0"))
        except InvalidOperation:
            problems.append(f"bad credit_limit_rupees '{row.get('credit_limit_rupees')}'")
        credit_days = 30
        if row.get("credit_days"):
            try:
                credit_days = int(row["credit_days"])
            except ValueError:
                problems.append(f"bad credit_days '{row['credit_days']}'")

        if problems:
            report.error(row_no, code, "; ".join(problems))
            continue
        seen_codes.add(code)

        existing = db.query(Customer).filter(Customer.code == code).first()
        if existing is None:
            report.created += 1
        else:
            report.updated += 1
        if dry_run:
            continue

        region = regions.get((row.get("region") or "").strip().lower())
        values = dict(
            business_name=name, primary_phone=phone,
            gstin=(row.get("gstin") or "").strip() or None,
            state=(row.get("state") or "").strip() or None,
            city=(row.get("city") or "").strip() or None,
            pincode=(row.get("pincode") or "").strip() or None,
            email=(row.get("email") or "").strip() or None,
            region_id=region.id if region else None,
            credit_limit_paise=credit_limit,
            credit_days=credit_days,
            language=(row.get("language") or "en").strip()[:5],
        )
        if existing is None:
            customer = Customer(code=code, created_by=actor_id, **values)
            db.add(customer)
            db.flush()
            # a dealer login for the primary phone (BR-AC-09)
            if not db.query(CustomerUser).filter(CustomerUser.phone == phone).first():
                db.add(CustomerUser(customer_id=customer.id, name=name, phone=phone,
                                    is_primary=True,
                                    language=values["language"]))
        else:
            for key, value in values.items():
                setattr(existing, key, value)
            existing.updated_by = actor_id
        if (row.get("distributor_code") or "").strip():
            pending_distributors.append((code, row["distributor_code"].strip()))

    if not dry_run:
        db.flush()
        # second pass: distributor hierarchy (BR-AN-03) once every row exists
        for dealer_code, distributor_code in pending_distributors:
            dealer = db.query(Customer).filter(Customer.code == dealer_code).first()
            distributor = db.query(Customer).filter(
                Customer.code == distributor_code).first()
            if dealer is not None and distributor is not None:
                dealer.distributor_id = distributor.id
            elif dealer is not None:
                report.errors.append(
                    f"{dealer_code}: distributor '{distributor_code}' not found")
        write_audit(db, actor_type="user", actor_id=actor_id, action="import.customers",
                    entity_type="import", entity_id="customers", after=report.as_dict())
        db.commit()
    return report


# ---------------- opening balances (P8-T2-02, BR-LED-05) ----------------
OPENING_COLUMNS = {"customer_code", "opening_balance_rupees"}


def import_opening_balances(db: Session, content: str, *, dry_run: bool,
                            actor_id: int) -> ImportReport:
    """Posts one `opening` ledger entry per customer. Re-running skips anyone
    who already has one — balances can never be double-posted."""
    report = ImportReport(kind="opening_balances", dry_run=dry_run)
    reader = _reader(content, OPENING_COLUMNS, "Opening balance")

    for row_no, row in enumerate(reader, start=2):
        report.total += 1
        code = (row.get("customer_code") or "").strip()
        customer = db.query(Customer).filter(Customer.code == code).first()
        if customer is None:
            report.error(row_no, code, "customer not found")
            continue
        try:
            amount = Money(_rupees_to_paise(row["opening_balance_rupees"]))
        except (InvalidOperation, KeyError):
            report.error(row_no, code, f"bad amount '{row.get('opening_balance_rupees')}'")
            continue

        already = (
            db.query(ledger.LedgerEntry)
            .filter(ledger.LedgerEntry.customer_id == customer.id,
                    ledger.LedgerEntry.entry_type == "opening")
            .first()
        )
        if already is not None:
            report.updated += 1        # skipped, already posted
            continue
        report.created += 1
        if dry_run:
            continue
        customer.opening_balance_paise = amount.paise
        ledger.post_opening_balance(db, customer, amount, actor_id)

    if not dry_run:
        write_audit(db, actor_type="user", actor_id=actor_id,
                    action="import.opening_balances", entity_type="import",
                    entity_id="opening_balances", after=report.as_dict())
        db.commit()
    return report


# ---------------- open invoices (P8-T2-05) ----------------
INVOICE_COLUMNS = {"customer_code", "invoice_no", "invoice_date", "amount_rupees"}


def import_open_invoices(db: Session, content: str, *, dry_run: bool,
                         actor_id: int) -> ImportReport:
    """Original invoice dates are preserved so ageing is real from day one.
    Optional: due_date, amount_paid_rupees."""
    report = ImportReport(kind="open_invoices", dry_run=dry_run)
    reader = _reader(content, INVOICE_COLUMNS, "Invoice")

    for row_no, row in enumerate(reader, start=2):
        report.total += 1
        code = (row.get("customer_code") or "").strip()
        invoice_no = (row.get("invoice_no") or "").strip()
        customer = db.query(Customer).filter(Customer.code == code).first()
        problems = []
        if customer is None:
            problems.append("customer not found")
        if not invoice_no:
            problems.append("invoice_no empty")
        try:
            invoice_date = date.fromisoformat((row.get("invoice_date") or "").strip())
        except ValueError:
            problems.append(f"bad invoice_date '{row.get('invoice_date')}'")
            invoice_date = date.today()
        try:
            total = _rupees_to_paise(row["amount_rupees"])
            paid = _rupees_to_paise(row.get("amount_paid_rupees", "0"))
            if total <= 0:
                problems.append("amount must be > 0")
            if paid > total:
                problems.append("amount_paid exceeds amount")
        except (InvalidOperation, KeyError):
            problems.append("bad amount")
            total = paid = 0

        if problems:
            report.error(row_no, invoice_no or code, "; ".join(problems))
            continue
        assert customer is not None

        existing = db.query(Invoice).filter(Invoice.invoice_no == invoice_no).first()
        if existing is not None:
            report.updated += 1        # re-run safe
            continue
        report.created += 1
        if dry_run:
            continue

        due_raw = (row.get("due_date") or "").strip()
        try:
            due_date = date.fromisoformat(due_raw) if due_raw else \
                invoice_date + timedelta(days=customer.credit_days)
        except ValueError:
            due_date = invoice_date + timedelta(days=customer.credit_days)

        db.add(Invoice(invoice_no=invoice_no, customer_id=customer.id,
                       invoice_date=invoice_date, due_date=due_date,
                       total_paise=total, amount_paid_paise=paid,
                       status="paid" if paid >= total else "open"))

    if not dry_run:
        write_audit(db, actor_type="user", actor_id=actor_id,
                    action="import.open_invoices", entity_type="import",
                    entity_id="open_invoices", after=report.as_dict())
        db.commit()
    return report


# ---------------- historical orders (P8-T2-04) ----------------
HISTORY_COLUMNS = {"customer_code", "order_no", "order_date", "design_no",
                   "length_ft", "breadth_ft", "quantity", "amount_rupees"}


def import_order_history(db: Session, content: str, *, dry_run: bool,
                         actor_id: int) -> ImportReport:
    """12 months of history so scoring and analytics mean something on day one.

    Historical rows are stored AS BILLED — the amount from the old books wins.
    We do not re-price history; that would rewrite what the dealer actually paid.
    """
    report = ImportReport(kind="order_history", dry_run=dry_run)
    reader = _reader(content, HISTORY_COLUMNS, "Order history")

    grouped: dict[str, list[dict]] = {}
    for row_no, row in enumerate(reader, start=2):
        report.total += 1
        order_no = (row.get("order_no") or "").strip()
        if not order_no:
            report.error(row_no, "", "order_no empty")
            continue
        grouped.setdefault(order_no, []).append({"row_no": row_no, **row})

    for order_no, lines in grouped.items():
        head = lines[0]
        code = (head.get("customer_code") or "").strip()
        customer = db.query(Customer).filter(Customer.code == code).first()
        if customer is None:
            report.error(head["row_no"], order_no, f"customer '{code}' not found")
            continue
        try:
            order_date = date.fromisoformat((head.get("order_date") or "").strip())
        except ValueError:
            report.error(head["row_no"], order_no, f"bad order_date '{head.get('order_date')}'")
            continue
        if db.query(Order).filter(Order.order_no == order_no).first() is not None:
            report.updated += 1        # re-run safe
            continue

        total = 0
        parsed_lines = []
        bad = False
        for line in lines:
            try:
                amount = _rupees_to_paise(line["amount_rupees"])
                length_ft = Decimal((line.get("length_ft") or "0").strip() or "0")
                breadth_ft = Decimal((line.get("breadth_ft") or "0").strip() or "0")
                quantity = int(line.get("quantity") or 1)
            except (InvalidOperation, ValueError, KeyError):
                report.error(line["row_no"], order_no, "bad line values")
                bad = True
                break
            total += amount
            parsed_lines.append((line, amount, length_ft, breadth_ft, quantity))
        if bad:
            continue

        report.created += 1
        if dry_run:
            continue

        order = Order(
            order_no=order_no, customer_id=customer.id,
            placed_by_type="user", placed_by_id=actor_id, channel="staff",
            status="CLOSED",                       # history is finished business
            order_date=order_date,
            subtotal_paise=total, taxable_paise=total, grand_total_paise=total,
            remarks="Imported from offline books", created_by=actor_id,
        )
        db.add(order)
        db.flush()
        for line, amount, length_ft, breadth_ft, quantity in parsed_lines:
            length_in = length_ft * 12
            breadth_in = breadth_ft * 12
            raw_sqft = (length_in * breadth_in / 144) if length_in and breadth_in else Decimal(0)
            db.add(OrderItem(
                order_id=order.id,
                design_no=(line.get("design_no") or "").strip(),
                design_name=(line.get("design_name") or line.get("design_no") or "").strip(),
                category=(line.get("category") or "Imported").strip(),
                length_in=length_in, breadth_in=breadth_in, quantity=quantity,
                raw_sqft=raw_sqft, billable_sqft=raw_sqft,
                line_area=raw_sqft * quantity,
                rate_paise=int(amount / max(quantity, 1)),
                rate_source="base", taxable_paise=amount, gst_pct=Decimal(0),
                line_total_paise=amount,
            ))

    if not dry_run:
        write_audit(db, actor_type="user", actor_id=actor_id,
                    action="import.order_history", entity_type="import",
                    entity_id="order_history", after=report.as_dict())
        db.commit()
    return report


# ---------------- reconciliation (P8-T2-09) ----------------
RECON_COLUMNS = {"customer_code", "book_balance_rupees"}


def reconcile(db: Session, content: str) -> dict:
    """Compare system outstanding against the owner's book figure, per customer.

    This is the report that decides whether the offline books can be retired.
    Read-only — it never writes.
    """
    reader = _reader(content, RECON_COLUMNS, "Reconciliation")
    rows = []
    matched = 0
    total_variance = 0
    for row_no, row in enumerate(reader, start=2):
        code = (row.get("customer_code") or "").strip()
        customer = db.query(Customer).filter(Customer.code == code).first()
        if customer is None:
            rows.append({"customer_code": code, "status": "MISSING_IN_SYSTEM",
                         "row": row_no})
            continue
        try:
            book = _rupees_to_paise(row["book_balance_rupees"])
        except (InvalidOperation, KeyError):
            rows.append({"customer_code": code, "status": "BAD_BOOK_VALUE", "row": row_no})
            continue
        system = ledger.derived_balance(db, customer.id).paise
        variance = system - book
        if variance == 0:
            matched += 1
        else:
            total_variance += abs(variance)
        rows.append({
            "customer_code": code,
            "customer": customer.business_name,
            "book_balance_paise": book,
            "system_balance_paise": system,
            "variance_paise": variance,
            "status": "MATCH" if variance == 0 else "VARIANCE",
        })

    variances = [r for r in rows if r.get("status") != "MATCH"]
    return {
        "checked": len(rows),
        "matched": matched,
        "variance_count": len(variances),
        "total_variance_paise": total_variance,
        "clean": len(variances) == 0,
        "variances": variances[:500],
    }


def customers_in_system_not_in_books(db: Session, book_codes: set[str]) -> list[str]:
    """The other half of reconciliation — customers the books do not mention."""
    codes = {c.code for c in db.query(Customer.code).filter(Customer.deleted_at.is_(None))}
    return sorted(codes - book_codes)


def import_summary(db: Session) -> dict:
    """Post-migration snapshot for the owner's sign-off (P8-T2-11)."""
    from app.modules.catalog.models import Design

    return {
        "customers": int(db.query(func.count(Customer.id))
                         .filter(Customer.deleted_at.is_(None)).scalar()),
        "designs": int(db.query(func.count(Design.id))
                       .filter(Design.deleted_at.is_(None)).scalar()),
        "orders": int(db.query(func.count(Order.id)).scalar()),
        "open_invoices": int(db.query(func.count(Invoice.id))
                             .filter(Invoice.status == "open").scalar()),
        "total_outstanding_paise": sum(
            ledger.derived_balance(db, c.id).paise
            for c in db.query(Customer).filter(Customer.deleted_at.is_(None)).all()
        ),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
