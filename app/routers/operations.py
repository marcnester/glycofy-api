from __future__ import annotations

import math
from datetime import datetime, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth_utils import get_current_user
from app.config import settings
from app.db import get_db
from app.models import AIOperationMetric, BetaFeedback, ProductEvent, SecurityAuditEvent, User, WeeklyPlanningJob
from app.services.usda_nutrition import USDANutritionError, lookup_food

router = APIRouter()


class FeedbackStatusIn(BaseModel):
    status: str


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


@router.get("/nutrition-source-health", tags=["operations"])
def nutrition_source_health(_admin: User = Depends(_require_admin)):
    """Admin-only live check that validates the configured USDA key and parser."""
    try:
        match = lookup_food("chicken breast meat only cooked roasted")
    except USDANutritionError as exc:
        raise HTTPException(status_code=503, detail="Authoritative nutrition source unavailable") from exc
    return {
        "status": "ok",
        "provider": "USDA FoodData Central",
        "required": settings.USDA_FDC_REQUIRED,
        "fdc_id": match.fdc_id,
        "data_type": match.data_type,
        "nutrients_present": sorted(match.nutrients_per_100g),
    }


@router.get("/security-summary", tags=["operations"])
def security_summary(
    hours: int = Query(24, ge=1, le=24 * 30),
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    since = datetime.utcnow() - timedelta(hours=hours)
    rows = db.query(SecurityAuditEvent).filter(SecurityAuditEvent.occurred_at >= since).all()
    by_severity: dict[str, int] = {}
    by_event: dict[str, int] = {}
    for row in rows:
        by_severity[row.severity] = by_severity.get(row.severity, 0) + 1
        key = f"{row.event_type}:{row.outcome}"
        by_event[key] = by_event.get(key, 0) + 1
    recent_alerts = (
        db.query(SecurityAuditEvent)
        .filter(SecurityAuditEvent.occurred_at >= since, SecurityAuditEvent.severity == "alert")
        .order_by(SecurityAuditEvent.occurred_at.desc())
        .limit(25)
        .all()
    )
    return {
        "window_hours": hours,
        "events": len(rows),
        "by_severity": by_severity,
        "by_event": by_event,
        "recent_alerts": [
            {
                "event_type": row.event_type,
                "outcome": row.outcome,
                "request_id": row.request_id,
                "occurred_at": row.occurred_at.isoformat(),
            }
            for row in recent_alerts
        ],
        "privacy": "Aggregates and request IDs only; no emails, IP addresses, meals, workouts, or health data.",
    }


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


@router.get("/beta-summary", tags=["operations"])
def beta_summary(
    days: int = Query(30, ge=1, le=180),
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    since = datetime.utcnow() - timedelta(days=days)
    counts = dict(
        db.query(ProductEvent.event_name, func.count(ProductEvent.id))
        .filter(ProductEvent.occurred_at >= since)
        .group_by(ProductEvent.event_name)
        .all()
    )
    active_users = (
        db.query(func.count(func.distinct(ProductEvent.user_id))).filter(ProductEvent.occurred_at >= since).scalar()
        or 0
    )
    return {
        "window_days": days,
        "active_users": active_users,
        "events": counts,
        "feedback": {
            "new": db.query(BetaFeedback).filter(BetaFeedback.status == "new").count(),
            "total": db.query(BetaFeedback).filter(BetaFeedback.created_at >= since).count(),
        },
        "privacy": "Aggregate funnel counts only; no user, meal, activity, or health data.",
    }


@router.get("/feedback", tags=["operations"])
def feedback_queue(
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    rows = db.query(BetaFeedback).order_by(BetaFeedback.created_at.desc()).limit(limit).all()
    return [
        {
            "id": row.id,
            "category": row.category,
            "rating": row.rating,
            "message": row.message,
            "page_path": row.page_path,
            "browser": row.browser_family,
            "viewport": row.viewport,
            "request_id": row.related_request_id or row.request_id,
            "status": row.status,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.patch("/feedback/{feedback_id}", tags=["operations"])
def update_feedback_status(
    feedback_id: int,
    payload: FeedbackStatusIn = Body(...),
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    if payload.status not in {"new", "reviewing", "resolved", "closed"}:
        raise HTTPException(status_code=422, detail="Invalid feedback status")
    row = db.get(BetaFeedback, feedback_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Feedback not found")
    row.status = payload.status
    db.commit()
    return {"id": row.id, "status": row.status}


@router.get("/failed-jobs", tags=["operations"])
def failed_jobs(
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    _admin: User = Depends(_require_admin),
):
    rows = (
        db.query(WeeklyPlanningJob)
        .filter(WeeklyPlanningJob.status == "failed")
        .order_by(WeeklyPlanningJob.completed_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "job_id": row.id,
            "stage": row.stage,
            "error_code": row.error_code,
            "error_reference": row.error_reference,
            "attempt_count": row.attempt_count,
            "created_at": row.created_at.isoformat(),
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }
        for row in rows
    ]
