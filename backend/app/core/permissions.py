"""RBAC — permission matrix and enforcement (BR-AC-01…08, R11).

The matrix below is the single source of the role → permission mapping,
seeded into the DB and enforced by the `require()` dependency in api deps.
UI hiding is cosmetic; THIS is the gate.
"""
from __future__ import annotations

# Permission codes (extend as modules land)
PERMISSIONS: dict[str, str] = {
    "design.read": "View catalogue",
    "design.write": "Create/edit designs and rate cards",
    "customer.read": "View customers",
    "customer.write": "Create/edit customers",
    "customer.limit": "Change credit limits / overrides / block / unblock",
    "order.create": "Place orders (incl. on behalf of a dealer)",
    "order.read": "View orders",
    "order.approve": "Approve over-limit / over-discount orders",
    "order.cancel": "Cancel orders",
    "order.status.production": "Update production stages",
    "order.status.dispatch": "Dispatch / deliver orders",
    "payment.record": "Record offline payments",
    "payment.confirm_cash": "Confirm cash received (the cash gate)",
    "payment.reverse": "Reverse payments (bounce/chargeback)",
    "invoice.write": "Create invoices / credit notes",
    "ledger.read": "View ledgers and statements",
    "credit.read": "View credit centre / ageing",
    "credit.simulate": "Run credit simulations",
    "report.read": "View analytics & reports",
    "report.export": "Export reports",
    "notification.manage": "Templates, resends, test-send",
    "settings.write": "Change business settings",
    "user.manage": "Manage users and roles",
    "audit.read": "Read the audit log",
    "import.run": "Run data imports",
}

# BR-AC-01…06. Dealers (customer_users) are scoped by token, not by this matrix.
ROLE_MATRIX: dict[str, set[str]] = {
    "super_admin": set(PERMISSIONS),  # BR-AC-01: everything
    "admin": {
        "design.read", "design.write", "customer.read", "customer.write", "customer.limit",
        "order.create", "order.read", "order.approve", "order.cancel",
        "order.status.production", "order.status.dispatch",
        "payment.record", "payment.confirm_cash", "payment.reverse",
        "invoice.write", "ledger.read", "credit.read", "credit.simulate",
        "report.read", "report.export", "notification.manage",
    },
    "accounts": {
        # BR-AC-03: money in, documents out — no rate-card edits
        "design.read", "customer.read", "payment.record", "payment.confirm_cash",
        "invoice.write", "ledger.read", "credit.read", "report.read", "report.export",
        "order.read",
    },
    "sales_rep": {
        # BR-AC-04: own customers only (scoping enforced in queries)
        "design.read", "customer.read", "order.create", "order.read",
        "payment.record", "credit.read", "report.read",
    },
    "production": {
        # BR-AC-05: specs only, no money visibility anywhere
        "order.read", "order.status.production",
    },
    "dispatch": {
        # BR-AC-06
        "order.read", "order.status.dispatch",
    },
}

ROLE_NAMES: dict[str, str] = {
    "super_admin": "Super Admin",
    "admin": "Admin / Manager",
    "accounts": "Accounts",
    "sales_rep": "Sales Representative",
    "production": "Production",
    "dispatch": "Dispatch",
}


def role_has(role_code: str, permission: str) -> bool:
    return permission in ROLE_MATRIX.get(role_code, set())
