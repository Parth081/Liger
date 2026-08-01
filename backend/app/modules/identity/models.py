"""Identity & access models (DATA_MODEL.md §1). BR-AC-01…09."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import ActorMixin, Base, PKMixin, TimestampMixin, VersionMixin

_FK_INT = BigInteger().with_variant(Integer, "sqlite")


class Role(Base, PKMixin, TimestampMixin):
    __tablename__ = "roles"

    # super_admin | admin | accounts | sales_rep | production | dispatch
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    is_system: Mapped[bool] = mapped_column(Boolean, default=True)

    permissions: Mapped[list[Permission]] = relationship(secondary="role_permissions", lazy="selectin")


class Permission(Base, PKMixin):
    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(String(80), unique=True)  # e.g. order.create, credit.override
    description: Mapped[str] = mapped_column(String(255), default="")


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("roles.id"), primary_key=True)
    permission_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("permissions.id"), primary_key=True)


class User(Base, PKMixin, TimestampMixin, ActorMixin, VersionMixin):
    """Staff and admins. Dealers live in CustomerUser."""

    __tablename__ = "users"

    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role_id: Mapped[int] = mapped_column(_FK_INT, ForeignKey("roles.id"), index=True)
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_2fa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    role: Mapped[Role] = relationship(lazy="joined")


class CustomerUser(Base, PKMixin, TimestampMixin, ActorMixin, VersionMixin):
    """Dealer login — phone + OTP, no password to leak (BR-AC-09).

    customer_id is a soft reference until the customers table lands in P1;
    the FK is added there.
    """

    __tablename__ = "customer_users"

    customer_id: Mapped[int | None] = mapped_column(_FK_INT, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    language: Mapped[str] = mapped_column(String(5), default="en")  # en|hi|gu (DEC-10)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OtpRequest(Base, PKMixin, TimestampMixin):
    """BR-AC-09: rate-limited via the (phone, created_at) index."""

    __tablename__ = "otp_requests"
    __table_args__ = (Index("ix_otp_phone_created", "phone", "created_at"),)

    phone: Mapped[str] = mapped_column(String(20))
    code_hash: Mapped[str] = mapped_column(String(255))
    purpose: Mapped[str] = mapped_column(String(20), default="login")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)


class RefreshToken(Base, PKMixin, TimestampMixin):
    """Rotating refresh tokens; only the hash is stored."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (UniqueConstraint("token_hash"),)

    subject_type: Mapped[str] = mapped_column(String(20))  # user | customer_user
    subject_id: Mapped[int] = mapped_column(_FK_INT, index=True)
    token_hash: Mapped[str] = mapped_column(String(255))
    device: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
