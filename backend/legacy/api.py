from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from typing import List

from app import crud, models, schemas, engines, auth
from app.database import get_db
from app.pricing import calculate_item_pricing

router = APIRouter()


# ==================== Auth ====================
@router.post("/auth/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    admin = db.query(models.AdminUser).filter(models.AdminUser.email == form_data.username).first()
    if not admin or not auth.verify_password(form_data.password, admin.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    token = auth.create_access_token(admin.id, admin.role)
    return {"access_token": token, "token_type": "bearer"}


# ==================== Designs (catalog browsing is public; writes are admin-only) ====================
@router.get("/designs/{design_no}", response_model=schemas.Design)
def get_design(design_no: str, db: Session = Depends(get_db)):
    design = crud.get_design_by_no(db, design_no)
    if not design:
        raise HTTPException(status_code=404, detail="Design not found")
    return design


@router.post("/designs/", response_model=schemas.Design)
def create_design(design: schemas.DesignCreate, db: Session = Depends(get_db),
                   admin: models.AdminUser = Depends(auth.get_current_admin)):
    return crud.create_design(db, design)


# ==================== Customers (admin-only for now — customer self-view comes with Phase 1 OTP auth) ====================
@router.get("/customers/{customer_id}", response_model=schemas.Customer)
def get_customer(customer_id: int, db: Session = Depends(get_db),
                  admin: models.AdminUser = Depends(auth.get_current_admin)):
    customer = crud.get_customer(db, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


@router.post("/customers/", response_model=schemas.Customer)
def create_customer(customer: schemas.CustomerCreate, db: Session = Depends(get_db),
                     admin: models.AdminUser = Depends(auth.get_current_admin)):
    return crud.create_customer(db, customer)


@router.get("/customers/{customer_id}/credit-status", response_model=schemas.CreditCheckResult)
def get_credit_status(customer_id: int, db: Session = Depends(get_db),
                       admin: models.AdminUser = Depends(auth.get_current_admin)):
    customer = crud.get_customer(db, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    due = engines.compute_customer_due(db, customer)
    available = round(customer.credit_limit - due, 2)
    return schemas.CreditCheckResult(
        allowed=customer.status != models.CustomerStatus.blocked,
        reason=customer.status.value,
        available_credit=available,
        outstanding_due=due,
    )


# ==================== Orders ====================
@router.post("/orders/", response_model=schemas.Order)
def create_order(order: schemas.OrderCreate, customer_id: int, db: Session = Depends(get_db)):
    """customer_id is required — every order must go through the credit engine,
    which cannot run without knowing whose credit to check."""
    try:
        return crud.create_order(db, order, customer_id, is_cash_payment=order.is_cash_payment)
    except crud.CreditBlockedError as e:
        raise HTTPException(status_code=403, detail=e.detail)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/orders/", response_model=List[schemas.Order])
def get_orders(customer_id: int = None, db: Session = Depends(get_db),
                admin: models.AdminUser = Depends(auth.get_current_admin)):
    query = db.query(models.Order)
    if customer_id is not None:
        query = query.filter(models.Order.customer_id == customer_id)
    return query.all()


# ==================== Payments ====================
@router.post("/payments/", response_model=schemas.Payment)
def create_payment(payment: schemas.PaymentCreate, db: Session = Depends(get_db)):
    """Records a payment attempt. Cash always lands as pending_confirmation —
    it does NOT free up credit until an admin confirms it via the endpoint below."""
    return crud.record_payment(
        db, payment.customer_id, payment.amount, payment.method,
        order_id=payment.order_id, notes=payment.notes,
    )


@router.post("/payments/{payment_id}/confirm", response_model=schemas.Payment)
def confirm_payment(payment_id: int, approve: bool = True, db: Session = Depends(get_db),
                     admin: models.AdminUser = Depends(auth.get_current_admin)):
    payment = crud.confirm_payment(db, payment_id, admin.id, approve=approve)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    return payment


# ==================== Cart ====================
@router.get("/cart/", response_model=List[schemas.CartItem])
def get_cart(customer_id: int = None, db: Session = Depends(get_db)):
    return crud.get_cart_items(db, customer_id=customer_id)


@router.post("/cart/", response_model=schemas.CartItem)
def add_to_cart(item: schemas.CartItemCreate, customer_id: int = None, db: Session = Depends(get_db)):
    return crud.add_to_cart(db, item, customer_id=customer_id)


@router.delete("/cart/")
def clear_cart(customer_id: int = None, db: Session = Depends(get_db)):
    return crud.clear_cart(db, customer_id=customer_id)


@router.get("/cart/total")
def get_cart_total(customer_id: int = None, db: Session = Depends(get_db)):
    total = crud.get_cart_total(db, customer_id=customer_id)
    return {"total": total}


# ==================== Live pricing preview ====================
@router.post("/calculate/")
def calculate_order(length: float, breadth: float, quantity: int, rate: float, db: Session = Depends(get_db)):
    """Same pricing.py function used by cart/order creation, so preview and
    final bill can never disagree."""
    try:
        return calculate_item_pricing(db, length, breadth, quantity, rate)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
