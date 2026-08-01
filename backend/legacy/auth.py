"""
Admin/staff authentication for Phase 0.

Customer-facing auth (phone + OTP) is deferred to Phase 1 because it depends
on the WhatsApp/SMS provider chosen in that phase — no point building OTP
delivery twice. For now, every admin-mutating endpoint requires this JWT.
"""
import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app import models
from app.database import get_db

SECRET_KEY = os.getenv("JWT_SECRET_KEY")
ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))

if not SECRET_KEY:
    raise RuntimeError("JWT_SECRET_KEY is not set. Add it to your .env — see .env.example.")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(admin_id: int, role: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=EXPIRE_MINUTES)
    payload = {"sub": str(admin_id), "role": role, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def get_current_admin(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> models.AdminUser:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        admin_id: Optional[str] = payload.get("sub")
        if admin_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    admin = db.query(models.AdminUser).filter(models.AdminUser.id == int(admin_id)).first()
    if admin is None or not admin.is_active:
        raise credentials_exception
    return admin


def require_super_admin(admin: models.AdminUser = Depends(get_current_admin)) -> models.AdminUser:
    if admin.role != "super_admin":
        raise HTTPException(status_code=403, detail="Super admin access required")
    return admin
