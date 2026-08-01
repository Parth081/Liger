from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean, Enum, Text
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
from app.database import Base


class CustomerStatus(str, enum.Enum):
    active = "active"
    warned = "warned"
    blocked = "blocked"


class PaymentMethod(str, enum.Enum):
    cash = "cash"
    card = "card"
    netbanking = "netbanking"


class PaymentStatus(str, enum.Enum):
    pending_confirmation = "pending_confirmation"
    confirmed = "confirmed"
    rejected = "rejected"


class OrderStatus(str, enum.Enum):
    draft = "draft"
    pending_confirmation = "pending_confirmation"
    confirmed = "confirmed"
    in_production = "in_production"
    ready = "ready"
    delivered = "delivered"
    cancelled = "cancelled"


class Design(Base):
    __tablename__ = "designs"

    id = Column(Integer, primary_key=True, index=True)
    design_no = Column(String, unique=True, index=True)
    name = Column(String)
    category = Column(String)
    rate_per_sqft = Column(Float)
    image_url = Column(String)  # primary/cover image; DesignImage below for gallery
    is_active = Column(Boolean, default=True)

    orders = relationship("OrderLineItem", back_populates="design")
    images = relationship("DesignImage", back_populates="design")


class DesignImage(Base):
    __tablename__ = "design_images"

    id = Column(Integer, primary_key=True, index=True)
    design_id = Column(Integer, ForeignKey("designs.id"))
    url = Column(String)
    sort_order = Column(Integer, default=0)

    design = relationship("Design", back_populates="images")


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    email = Column(String, unique=True, index=True, nullable=True)
    phone = Column(String, unique=True, index=True)

    # --- Credit engine fields (previously missing -> engines.py was crashing) ---
    credit_limit = Column(Float, default=50000.0)
    opening_due = Column(Float, default=0.0)          # legacy/manual due carried in from offline books
    cash_bonus_pct = Column(Float, default=0.0)        # extra limit % granted on confirmed cash payment history
    status = Column(Enum(CustomerStatus), default=CustomerStatus.active)
    blocked_at = Column(DateTime, nullable=True)
    block_reason = Column(String, nullable=True)
    unblocked_at = Column(DateTime, nullable=True)
    warning_count = Column(Integer, default=0)  # how many of the 2 pre-block warnings have fired

    # --- Analytics fields ---
    region = Column(String, nullable=True)
    distributor_name = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    orders = relationship("Order", back_populates="customer")
    payments = relationship("Payment", back_populates="customer")


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"))
    order_date = Column(DateTime, default=datetime.utcnow)
    status = Column(Enum(OrderStatus), default=OrderStatus.pending_confirmation)
    total_amount = Column(Float)

    customer = relationship("Customer", back_populates="orders")
    line_items = relationship("OrderLineItem", back_populates="order")
    payments = relationship("Payment", back_populates="order")


class OrderLineItem(Base):
    __tablename__ = "order_line_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"))
    design_id = Column(Integer, ForeignKey("designs.id"), nullable=True)
    design_no = Column(String)
    length = Column(Float)
    breadth = Column(Float)
    quantity = Column(Integer)
    raw_sqft = Column(Float)
    billable_sqft = Column(Float)
    rate_per_sqft = Column(Float)
    amount = Column(Float)

    order = relationship("Order", back_populates="line_items")
    design = relationship("Design", back_populates="orders")


class Cart(Base):
    __tablename__ = "cart"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    design_no = Column(String)
    length = Column(Float)
    breadth = Column(Float)
    quantity = Column(Integer)
    raw_sqft = Column(Float)
    billable_sqft = Column(Float)
    rate_per_sqft = Column(Float)
    amount = Column(Float)
    added_at = Column(DateTime, default=datetime.utcnow)


class Payment(Base):
    """Ledger entry. A customer's due is DERIVED from orders vs confirmed payments,
    never stored as a single mutable counter."""
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)  # nullable: can pay against total due
    amount = Column(Float)
    method = Column(Enum(PaymentMethod))
    status = Column(Enum(PaymentStatus), default=PaymentStatus.pending_confirmation)
    confirmed_by_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    confirmed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    notes = Column(String, nullable=True)

    customer = relationship("Customer", back_populates="payments")
    order = relationship("Order", back_populates="payments")


class CreditEvent(Base):
    """Audit trail for every block / unblock / limit change / warning."""
    __tablename__ = "credit_events"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), index=True)
    event_type = Column(String)  # warned, blocked, unblocked, limit_changed
    reason = Column(String, nullable=True)
    actor_admin_id = Column(Integer, ForeignKey("admin_users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class AdminUser(Base):
    __tablename__ = "admin_users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    role = Column(String, default="staff")  # super_admin | staff
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    channel = Column(String)  # whatsapp | sms
    template = Column(String)
    payload = Column(Text, nullable=True)
    status = Column(String, default="pending")  # pending | sent | failed
    related_order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    sent_at = Column(DateTime, nullable=True)


class Setting(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, index=True)
    value = Column(String)
