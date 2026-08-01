"""Auth endpoints (API_SPEC §1). Thin router — validation in, service call, response out."""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.api.v1.deps import Actor, get_actor, get_db
from app.core.permissions import ROLE_MATRIX
from app.modules.identity import service

router = APIRouter(prefix="/auth", tags=["auth"])


class StaffLoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class TwoFAIn(BaseModel):
    challenge_token: str
    code: str = Field(min_length=6, max_length=8)


class OtpRequestIn(BaseModel):
    phone: str = Field(min_length=10, max_length=15, pattern=r"^\+?\d+$")


class OtpVerifyIn(BaseModel):
    phone: str = Field(min_length=10, max_length=15, pattern=r"^\+?\d+$")
    code: str = Field(min_length=4, max_length=8)


class RefreshIn(BaseModel):
    refresh_token: str


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/staff/login")
def staff_login(body: StaffLoginIn, request: Request, db: Session = Depends(get_db)):
    return service.staff_login(db, body.email, body.password, ip=_ip(request))


@router.post("/staff/2fa")
def staff_2fa(body: TwoFAIn, request: Request, db: Session = Depends(get_db)):
    return service.staff_verify_2fa(db, body.challenge_token, body.code, ip=_ip(request))


@router.post("/otp/request")
def otp_request(body: OtpRequestIn, request: Request, db: Session = Depends(get_db)):
    return service.request_otp(db, body.phone, ip=_ip(request))


@router.post("/otp/verify")
def otp_verify(body: OtpVerifyIn, request: Request, db: Session = Depends(get_db)):
    return service.verify_otp(db, body.phone, body.code, ip=_ip(request))


@router.post("/refresh")
def refresh(body: RefreshIn, request: Request, db: Session = Depends(get_db)):
    return service.refresh_tokens(db, body.refresh_token, ip=_ip(request))


@router.post("/logout")
def logout(body: RefreshIn, db: Session = Depends(get_db)):
    service.logout(db, body.refresh_token)
    return {"ok": True}


@router.get("/me")
def me(actor: Actor = Depends(get_actor)):
    if actor.is_dealer:
        return {
            "type": "customer_user",
            "name": actor.customer_user.name if actor.customer_user else None,
            "role": "customer",
            "customer_id": actor.customer_id,
            "permissions": [],
        }
    return {
        "type": "user",
        "name": actor.user.name if actor.user else None,
        "role": actor.role,
        "customer_id": None,
        "permissions": sorted(ROLE_MATRIX.get(actor.role, set())),
    }
