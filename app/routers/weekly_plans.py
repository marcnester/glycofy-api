"""Compatibility endpoint for clients using the original weekly planner."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.rate_limit import AUTH_LIMITER
from app.routers.auth import get_current_user
from app.routers.plans import PlanCreateIn, create_or_replace_plan, get_plan

router = APIRouter(prefix="/v1/plan", tags=["plans-weekly"])
logger = logging.getLogger(__name__)


@router.post("/week/generate")
async def generate_weekly_plan(
    start: date | None = Query(None),
    days: int = Query(7, ge=1, le=14),
    engine: Literal["llm", "heuristic"] = Query("llm"),
    replace: bool = Query(False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Generate owner-scoped plans without forwarding cookies or network requests."""
    AUTH_LIMITER.check(f"legacy-weekly-plan:{user.id}", maximum=2, window_seconds=900)
    start = start or date.today()
    if start > date.max - timedelta(days=days - 1):
        raise HTTPException(status_code=422, detail="Plan dates exceed supported range")
    results: list[dict] = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        try:
            if replace:
                await create_or_replace_plan(
                    date=day.isoformat(),
                    payload=PlanCreateIn(),
                    engine=engine,
                    replace=True,
                    db=db,
                    user=user,
                )
            else:
                await get_plan(date=day.isoformat(), create_if_missing=True, engine=engine, db=db, user=user)
            results.append({"date": day.isoformat(), "ok": True, "status": 200})
        except Exception:
            db.rollback()
            logger.exception("legacy_weekly_plan_failed")
            # Internal exceptions and provider responses can contain secrets or
            # athlete data. Return only a fixed, safe failure code.
            results.append({"date": day.isoformat(), "ok": False, "status": 503, "error": "daily_plan_failed"})

    response = {
        "start": start.isoformat(),
        "days": days,
        "engine": engine,
        "replace": replace,
        "user_id": user.id,
        "results": results,
    }
    if not any(result["ok"] for result in results):
        raise HTTPException(status_code=502, detail={"message": "Weekly generation failed for all days", **response})
    return response
