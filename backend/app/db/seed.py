"""Seed roles, permissions and settings (P0-T1-04/05). Idempotent."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core import security
from app.core.permissions import PERMISSIONS, ROLE_MATRIX, ROLE_NAMES
from app.core.settings_registry import seed_settings
from app.modules.identity.models import Permission, Role, RolePermission, User


def seed_rbac(db: Session) -> None:
    existing_perms = {p.code: p for p in db.query(Permission).all()}
    for code, desc in PERMISSIONS.items():
        if code not in existing_perms:
            p = Permission(code=code, description=desc)
            db.add(p)
            existing_perms[code] = p
    db.flush()

    existing_roles = {r.code: r for r in db.query(Role).all()}
    for role_code, perm_codes in ROLE_MATRIX.items():
        role = existing_roles.get(role_code)
        if role is None:
            role = Role(code=role_code, name=ROLE_NAMES[role_code], is_system=True)
            db.add(role)
            db.flush()
            existing_roles[role_code] = role
        current = {
            rp.permission_id
            for rp in db.query(RolePermission).filter(RolePermission.role_id == role.id).all()
        }
        for pc in perm_codes:
            pid = existing_perms[pc].id
            if pid not in current:
                db.add(RolePermission(role_id=role.id, permission_id=pid))
    db.commit()


def seed_super_admin(db: Session, email: str, password: str, name: str = "Owner") -> User:
    """Create the first super admin if none exists. Local/staging convenience;
    production uses a one-time CLI invocation."""
    user = db.query(User).filter(User.email == email).first()
    if user is not None:
        return user
    role = db.query(Role).filter(Role.code == "super_admin").one()
    user = User(name=name, email=email, password_hash=security.hash_password(password), role_id=role.id)
    db.add(user)
    db.commit()
    return user


def seed_categories(db: Session) -> None:
    from app.modules.catalog.models import DEFAULT_CATEGORIES, Category

    existing = {c.code for c in db.query(Category.code).all()}
    for order, (name, code, product_type) in enumerate(DEFAULT_CATEGORIES):
        if code not in existing:
            db.add(Category(name=name, code=code, product_type=product_type, sort_order=order))
    db.commit()


def seed_all(db: Session) -> None:
    from app.modules.notifications.templates import seed_templates

    seed_settings(db)
    seed_rbac(db)
    seed_categories(db)
    seed_templates(db)
