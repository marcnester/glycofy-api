# app/routers/imports.py
from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from io import StringIO
from typing import Any

import requests
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.deps import get_current_user  # <- FIX: import from app.deps
from app.models import Activity, OAuthAccount, User

router = APIRouter()

STRAVA_BASE = "https://www.strava.com/api/v3"


# -------------------------------
# Helpers
# -------------------------------
def _now_epoch() -> int:
    return int(time.time())


def _parse_since(since: str | None) -> date:
    """Accept YYYY-MM-DD; default to first day of current month (UTC)."""
    if since:
        try:
            # strict date only
            return datetime.strptime(since, "%Y-%m-%d").date()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid 'since' format; expected YYYY-MM-DD",
            )
    # default: first of current UTC month
    now_utc = datetime.now(tz=UTC).date()
    return now_utc.replace(day=1)


def _date_to_utc_epoch(d: date) -> int:
    """Convert a date to 00:00:00 UTC epoch seconds."""
    dt = datetime(d.year, d.month, d.day, tzinfo=UTC)
    return int(dt.timestamp())


def _ensure_strava_oauth(db: Session, user_id: int) -> OAuthAccount:
    acct: OAuthAccount | None = (
        db.query(OAuthAccount).filter(OAuthAccount.user_id == user_id, OAuthAccount.provider == "strava").first()
    )
    if not acct:
        raise HTTPException(status_code=400, detail="Strava is not linked for this user.")
    if not acct.access_token:
        raise HTTPException(status_code=400, detail="Strava token missing; relink account.")
    return acct


def _refresh_strava_token(db: Session, acct: OAuthAccount) -> None:
    """
    Refresh if token is expired or about to expire in <= 60 seconds.
    Updates the DB record in-place.
    """
    # If we have >= 60 seconds left, skip refresh
    if acct.expires_at and acct.expires_at - _now_epoch() > 60:
        return

    resp = requests.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": settings.STRAVA_CLIENT_ID,
            "client_secret": settings.STRAVA_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": acct.refresh_token or "",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to refresh Strava token: {resp.status_code} {resp.text}",
        )
    data = resp.json()
    acct.access_token = data.get("access_token")
    acct.refresh_token = data.get("refresh_token", acct.refresh_token)
    acct.expires_at = int(data.get("expires_at") or _now_epoch() + 21600)
    acct.scope = data.get("scope", acct.scope)
    acct.updated_at = datetime.utcnow()
    db.add(acct)
    db.commit()


def _fetch_strava_activities(access_token: str, after_epoch: int) -> list[dict[str, Any]]:
    """
    Pull activities since 'after_epoch' (UTC) with pagination.
    """
    all_items: list[dict[str, Any]] = []
    page = 1
    per_page = 100

    headers = {"Authorization": f"Bearer {access_token}"}

    while True:
        resp = requests.get(
            f"{STRAVA_BASE}/athlete/activities",
            params={"after": after_epoch, "per_page": per_page, "page": page},
            headers=headers,
            timeout=30,
        )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Strava API error: {resp.status_code} {resp.text}",
            )
        batch = resp.json()
        if not isinstance(batch, list) or not batch:
            break
        all_items.extend(batch)
        if len(batch) < per_page:
            break
        page += 1

    return all_items


def _map_strava_to_activity_fields(s: dict[str, Any]) -> dict[str, Any]:
    """
    Convert Strava activity JSON to our Activity fields.
    """
    start_iso = s.get("start_date")  # e.g., '2025-10-18T16:43:59Z'
    try:
        start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00")) if start_iso else datetime.utcnow()
    except Exception:
        start_dt = datetime.utcnow()

    duration_s = int(s.get("elapsed_time") or 0)
    distance_m = float(s.get("distance") or 0.0)

    kcal = None
    if s.get("kilojoules") is not None:
        try:
            # Rough proxy: 1 kJ ~ 1 kcal for cycling/running power files
            kcal = int(round(float(s["kilojoules"])))
        except Exception:
            kcal = None

    sport = s.get("sport_type") or s.get("type") or "Workout"

    return {
        "sport": sport,
        "start_time": start_dt,
        "duration_s": duration_s,
        "kcal": kcal,
        "distance_m": distance_m,
        "source_provider": "strava",
        "source_id": str(s.get("id") or s.get("external_id") or ""),
    }


def _upsert_activity(db: Session, user_id: int, fields: dict[str, Any]) -> tuple[str, Activity | None]:
    """
    Insert or update by (user_id, source_provider, source_id).
    Returns ("created"|"updated"|"skipped", Activity|None)
    """
    if not fields.get("source_id"):
        return ("skipped", None)  # nothing to key on

    existing: Activity | None = (
        db.query(Activity)
        .filter(
            Activity.user_id == user_id,
            Activity.source_provider == "strava",
            Activity.source_id == fields["source_id"],
        )
        .first()
    )

    if existing:
        changed = False
        for k, v in fields.items():
            if k in ("source_provider", "source_id"):
                continue
            if getattr(existing, k) != v:
                setattr(existing, k, v)
                changed = True
        if changed:
            db.add(existing)
            return ("updated", existing)
        return ("skipped", existing)

    obj = Activity(user_id=user_id, **fields, created_at=datetime.utcnow())
    db.add(obj)
    return ("created", obj)


# -------------------------------
# Routes — Strava
# -------------------------------


@router.post("/strava/sync")
def strava_sync(
    since: str | None = Query(
        default=None,
        description="ISO date (YYYY-MM-DD). If omitted, defaults to first day of current month (UTC).",
    ),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Pull Strava activities since a given date and upsert into DB.
    """
    since_date = _parse_since(since)
    after_epoch = _date_to_utc_epoch(since_date)

    # OAuth
    acct = _ensure_strava_oauth(db, user.id)
    _refresh_strava_token(db, acct)

    # Fetch
    items = _fetch_strava_activities(acct.access_token, after_epoch)

    # Upsert
    created = updated = skipped = 0
    for s in items:
        fields = _map_strava_to_activity_fields(s)
        status_str, _obj = _upsert_activity(db, user.id, fields)
        if status_str == "created":
            created += 1
        elif status_str == "updated":
            updated += 1
        else:
            skipped += 1
    db.commit()

    return {
        "since": since_date.isoformat(),
        "fetched": len(items),
        "created": created,
        "updated": updated,
        "skipped": skipped,
    }


@router.get("/strava/status")
def strava_status(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Simple status endpoint the UI can ping.
    """
    configured = bool(settings.STRAVA_CLIENT_ID and settings.STRAVA_CLIENT_SECRET and settings.STRAVA_REDIRECT_URI)
    acct = db.query(OAuthAccount).filter(OAuthAccount.user_id == user.id, OAuthAccount.provider == "strava").first()
    linked = bool(acct and acct.access_token)
    return {
        "strava": {
            "configured": configured,
            "linked": linked,
            "external_athlete_id": acct.external_athlete_id if acct else None,
            "scope": acct.scope if acct else None,
            "expires_at": acct.expires_at if acct else None,
        }
    }


# -------------------------------
# Routes — CSV Export
# -------------------------------


def _parse_date_opt(s: str | None) -> datetime | None:
    """Accept YYYY-MM-DD or full ISO; returns timezone-naive datetime."""
    if not s:
        return None
    try:
        if len(s) == 10:
            return datetime.strptime(s, "%Y-%m-%d")
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid date: {s}")


@router.get("/export/activities.csv", summary="Export activities as CSV")
def export_activities_csv(
    from_: str | None = Query(None, alias="from", description="Start date (YYYY-MM-DD)"),
    to: str | None = Query(None, description="End date (YYYY-MM-DD)"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """
    Streams a CSV of the user's activities. If no range is supplied, defaults to last 30 days.
    """
    now = datetime.now()
    start_dt = _parse_date_opt(from_) or (now - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
    end_dt = _parse_date_opt(to) or now
    # make `to` inclusive for date-only input
    if to and len(to) == 10:
        end_dt = end_dt + timedelta(days=1)

    q = (
        db.query(Activity)
        .filter(Activity.user_id == user.id)
        .filter(Activity.start_time >= start_dt)
        .filter(Activity.start_time < end_dt)
        .order_by(Activity.start_time.desc())
    )
    rows: Iterable[Activity] = q.all()

    buf = StringIO()
    # header
    buf.write("date,start_time,type,duration_s,kcal,distance_m,provider,source_id\n")
    for a in rows:
        date_only = a.start_time.date().isoformat() if a.start_time else ""
        start_iso = a.start_time.isoformat() if a.start_time else ""
        sport = a.sport or ""
        dur = int(a.duration_s or 0)
        kcal = int(a.kcal or 0)
        dist = float(a.distance_m or 0.0)
        provider = a.source_provider or ""
        sid = a.source_id or ""

        def esc(v: Any) -> str:
            s = str(v)
            if any(c in s for c in [",", '"', "\n"]):
                s = '"' + s.replace('"', '""') + '"'
            return s

        buf.write(
            ",".join(
                [
                    esc(date_only),
                    esc(start_iso),
                    esc(sport),
                    str(dur),
                    str(kcal),
                    str(dist),
                    esc(provider),
                    esc(sid),
                ]
            )
            + "\n"
        )

    buf.seek(0)
    # If caller provided date-only `to`, we subtracted one day above for filename end cap
    end_for_name = end_dt - timedelta(seconds=1)
    filename = f"activities_{start_dt.date().isoformat()}_{end_for_name.date().isoformat()}.csv"
    return StreamingResponse(
        buf, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )
