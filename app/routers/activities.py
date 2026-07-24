# app/routers/activities.py
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.auth_utils import get_current_user
from app.db import get_db
from app.models import Activity, User

router = APIRouter()

# ---------------------------
# Helpers
# ---------------------------


def _safe_iso(val: Any) -> str | None:
    """Return an ISO-8601 string if val is a datetime or a recognizable string; else None."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, str):
        s = val.strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
            try:
                return datetime.strptime(s, fmt).isoformat()
            except ValueError:
                pass
        # Already ISO-ish or unknown → return as-is
        return s
    if isinstance(val, (int, float)):
        try:
            return datetime.fromtimestamp(val).isoformat()
        except Exception:
            return None
    return None


def _to_dict(a: Activity) -> dict[str, Any]:
    return {
        "id": a.id,
        "user_id": a.user_id,
        "provider": a.provider,
        "source_id": a.source_id,
        "source_provider": a.source_provider,
        "sport": a.sport,
        "start_time": _safe_iso(a.start_time),
        "duration_s": a.duration_s,
        "distance_m": a.distance_m,
        "avg_hr": getattr(a, "avg_hr", None),
        "kcal": a.kcal,
        "created_at": _safe_iso(a.created_at),
    }


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Bad date '{s}', expected YYYY-MM-DD")


# ---------------------------
# Routes
# ---------------------------


# NOTE: app.main mounts this router with prefix="/activities"
# so this endpoint becomes GET /activities
@router.get("")
def list_activities(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=250),
    from_: str | None = Query(default=None, alias="from", description="YYYY-MM-DD (inclusive)"),
    to_: str | None = Query(default=None, alias="to", description="YYYY-MM-DD (inclusive)"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Paginated list of the authenticated user's activities with optional date range filters."""
    day_from = _parse_date(from_)
    day_to = _parse_date(to_)

    q = db.query(Activity).filter(Activity.user_id == user.id)

    if day_from:
        q = q.filter(Activity.start_time >= datetime.combine(day_from, datetime.min.time()))
    if day_to:
        q = q.filter(Activity.start_time <= datetime.combine(day_to, datetime.max.time()))

    total = q.count()
    q = q.order_by(Activity.start_time.desc())

    items: list[Activity] = q.offset((page - 1) * page_size).limit(page_size).all()

    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": [_to_dict(a) for a in items],
    }


# Final URL: GET /activities/csv
@router.get("/csv")
def list_activities_csv(
    from_: str | None = Query(default=None, alias="from", description="YYYY-MM-DD (inclusive)"),
    to_: str | None = Query(default=None, alias="to", description="YYYY-MM-DD (inclusive)"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """CSV export (non-paginated) for the user's activities in an optional date range."""
    day_from = _parse_date(from_)
    day_to = _parse_date(to_)

    q = db.query(Activity).filter(Activity.user_id == user.id)
    if day_from:
        q = q.filter(Activity.start_time >= datetime.combine(day_from, datetime.min.time()))
    if day_to:
        q = q.filter(Activity.start_time <= datetime.combine(day_to, datetime.max.time()))
    q = q.order_by(Activity.start_time.desc())

    rows = q.all()

    import csv
    from io import StringIO

    buf = StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "id",
            "start_time",
            "provider",
            "source_provider",
            "source_id",
            "sport",
            "duration_s",
            "distance_m",
            "avg_hr",
            "kcal",
            "created_at",
        ]
    )
    for a in rows:
        w.writerow(
            [
                a.id,
                _safe_iso(a.start_time) or "",
                a.provider or "",
                a.source_provider or "",
                a.source_id or "",
                a.sport or "",
                a.duration_s if a.duration_s is not None else "",
                a.distance_m if a.distance_m is not None else "",
                getattr(a, "avg_hr", "") if getattr(a, "avg_hr", None) is not None else "",
                a.kcal if a.kcal is not None else "",
                _safe_iso(a.created_at) or "",
            ]
        )

    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=activities.csv"},
    )
