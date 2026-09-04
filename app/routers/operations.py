from __future__ import annotations

import math
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth_utils import get_current_user
from app.config import settings
from app.db import get_db
from app.models import AIOperationMetric, User, WeeklyPlanningJob

router = APIRouter()


def _require_admin(user: User = Depends(get_current_user)) -> User:
    allowed = {email.casefold() for email in settings.csv_values(settings.ADMIN_EMAILS)}
    if not allowed or user.email.casefold() not in allowed:
        raise HTTPException(status_code=404, detail="Not found")
    return user


def _percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)]


@router.get("/ai-summary", tags=["operations"])
def ai_summary(
    hours: int = Query(168, ge=1, le=24 * 90),
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    since = datetime.utcnow() - timedelta(hours=hours)
    rows = db.query(AIOperationMetric).filter(AIOperationMetric.occurred_at >= since).all()
    latencies = [row.latency_ms for row in rows if row.latency_ms is not None]
    successes = sum(row.status in {"success", "partial"} for row in rows)
    failures = sum(row.status in {"failed", "parse_error"} for row in rows)
    by_operation: dict[str, dict[str, int | float]] = {}
    for row in rows:
        bucket = by_operation.setdefault(row.operation, {"requests": 0, "failures": 0, "cost_usd": 0.0})
        bucket["requests"] = int(bucket["requests"]) + 1
        bucket["failures"] = int(bucket["failures"]) + int(row.status in {"failed", "parse_error"})
        bucket["cost_usd"] = round(float(bucket["cost_usd"]) + row.estimated_cost_usd, 6)
    active_jobs = db.query(WeeklyPlanningJob).filter(WeeklyPlanningJob.status.in_(("queued", "running"))).count()
    failed_jobs = (
        db.query(WeeklyPlanningJob)
        .filter(WeeklyPlanningJob.status == "failed", WeeklyPlanningJob.completed_at >= since)
        .count()
    )
    return {
        "window_hours": hours,
        "requests": len(rows),
        "successes": successes,
        "failures": failures,
        "failure_rate": round(failures / len(rows), 4) if rows else 0.0,
        "latency_ms": {"p50": _percentile(latencies, 0.5), "p95": _percentile(latencies, 0.95)},
        "tokens": {
            "input": sum(row.input_tokens or 0 for row in rows),
            "output": sum(row.output_tokens or 0 for row in rows),
        },
        "estimated_cost_usd": round(sum(row.estimated_cost_usd for row in rows), 6),
        "weekly_jobs": {"active": active_jobs, "failed": failed_jobs},
        "by_operation": by_operation,
        "privacy": "Aggregates only; no prompts, meals, health fields, IP addresses, or user identifiers.",
    }
