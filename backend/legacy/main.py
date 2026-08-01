import os
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.database import engine, get_db, Base
from app import models
from app.api import router

# NOTE: In production, table creation is handled by Alembic migrations
# (see alembic/), not create_all(). create_all() is left here ONLY as a
# convenience for local dev and is skipped when ALEMBIC_MANAGED=1.
if os.getenv("ALEMBIC_MANAGED") != "1":
    Base.metadata.create_all(bind=engine)

app = FastAPI(title="Liger Order & Credit API")

allowed_origins = os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/")
def read_root():
    return {"message": "Liger API is running!"}


@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    try:
        db.execute("SELECT 1")
        db_status = "connected"
    except Exception:
        db_status = "unreachable"
    return {"status": "healthy", "database": db_status}


@app.post("/api/v1/seed-settings")
def seed_settings(db: Session = Depends(get_db)):
    """Initializes the Settings table with Liger's core rules. Safe to call
    repeatedly — only inserts keys that don't already exist."""
    settings_to_add = [
        models.Setting(key="min_billable_sqft", value="11"),
        models.Setting(key="grace_days", value="7"),
        models.Setting(key="default_cash_bonus_pct", value="15"),
    ]
    for setting in settings_to_add:
        exists = db.query(models.Setting).filter(models.Setting.key == setting.key).first()
        if not exists:
            db.add(setting)
    db.commit()
    return {"message": "Settings initialized successfully"}
