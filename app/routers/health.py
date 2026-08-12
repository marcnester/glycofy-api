# app/routers/health.py
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import get_db

router = APIRouter()


class HealthOut(BaseModel):
    status: str
    ts: float


@router.get("/health", response_model=HealthOut, name="health", include_in_schema=False)
def health() -> HealthOut:
    """
    Simple liveness probe.
    """
    return HealthOut(status="ok", ts=time.time())


@router.get("/ready", response_model=HealthOut, name="ready", include_in_schema=False)
def ready(db: Session = Depends(get_db)) -> HealthOut:
    """
    Readiness probe. A web process is not ready to serve authenticated
    requests until it can reach the primary database.
    """
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service is not ready",
        ) from exc
    return HealthOut(status="ready", ts=time.time())
