from pydantic import BaseModel, EmailStr
from datetime import datetime
from typing import Optional, List
from app.models import CustomerStatus, PaymentMethod, PaymentStatus, OrderStatus


# ---------------- Design ----------------
class DesignBase(BaseModel):
    design_no: str
    name: str
    rate_per_sqft: float
    image_url: Optional[str] = None
    category: Optional[str] = None


class DesignCreate(DesignBase):
    pass


class Design(DesignBase):
    id: int
    is_active: bool = True

    class Config:
        from_attributes = True


# ---------------- Order line items ----------------
class OrderLineItemBase(BaseModel):
    design_no: str
    length: float
    breadth: float
    quantity: int
    rate_per_sqft: float


class OrderLineItemCreate(OrderLineItemBase):
    pass


class OrderLineItem(OrderLineItemBase):
    id: int
    order_id: int
    raw_sqft: float
    billable_sqft: float
    amount: float

    class Config:
        from_attributes = True


# ---------------- Orders ----------------
class OrderBase(BaseModel):
    status: Optional[OrderStatus] = OrderStatus.pending_confirmation


class OrderCreate(BaseModel):
    line_items: List[OrderLineItemCreate]
    is_cash_payment: bool = False  # informs the credit engine's cash-bonus limit (R5)


class Order(OrderBase):
    id: int
    customer_id: int
    order_date: datetime
    total_amount: float
    line_items: List[OrderLineItem] = []

    class Config:
        from_attributes = True


# ---------------- Customers ----------------
class CustomerBase(BaseModel):
    name: str
    email: Optional[EmailStr] = None
    phone: str
    credit_limit: float = 50000.0
    cash_bonus_pct: float = 0.0
    region: Optional[str] = None
    distributor_name: Optional[str] = None


class CustomerCreate(CustomerBase):
    pass


class Customer(CustomerBase):
    id: int
    opening_due: float = 0.0
    status: CustomerStatus = CustomerStatus.active

    class Config:
        from_attributes = True


class CreditCheckResult(BaseModel):
    allowed: bool
    reason: Optional[str] = None
    message: Optional[str] = None
    available_credit: Optional[float] = None
    outstanding_due: Optional[float] = None


# ---------------- Payments ----------------
class PaymentCreate(BaseModel):
    customer_id: int
    order_id: Optional[int] = None
    amount: float
    method: PaymentMethod
    notes: Optional[str] = None


class Payment(BaseModel):
    id: int
    customer_id: int
    order_id: Optional[int]
    amount: float
    method: PaymentMethod
    status: PaymentStatus
    confirmed_by_admin_id: Optional[int]
    confirmed_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


# ---------------- Cart ----------------
class CartItemBase(BaseModel):
    design_no: str
    length: float
    breadth: float
    quantity: int
    rate_per_sqft: float


class CartItemCreate(CartItemBase):
    pass


class CartItem(CartItemBase):
    id: int
    raw_sqft: float
    billable_sqft: float
    amount: float
    added_at: datetime

    class Config:
        from_attributes = True
