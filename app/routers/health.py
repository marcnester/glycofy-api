# app/routers/health.py
from __future__ import annotations

import time

from fastapi import APIRouter
from pydantic import BaseModel

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
def ready() -> HealthOut:
    """
    Simple readiness probe (extend with DB checks if desired).
    """
    return HealthOut(status="ready", ts=time.time())
