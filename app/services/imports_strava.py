# app/services/imports_strava.py
"""
Pulls activities from Strava into our activities table.

- Requires oauth_accounts with provider='strava' for the user
- Refreshes tokens if expired
- Paginates through /athlete/activities
- Upserts by (user_id, source_provider, source_id)
"""

from __future__ import annotations

import time
from datetime import date, datetime
from typing import Any

import requests
from sqlalchemy.orm import Session

from app.models import Activity, OAuthAccount, User
from app.services.strava_client import STRAVA_API_BASE, refresh_access_token


def _now_epoch() -> int:
    return int(time.time())


def _needs_refresh(acct: OAuthAccount) -> bool:
    exp = acct.expires_at or 0
    # refresh 60s early
    return exp <= (_now_epoch() + 60)


def _ensure_token(db: Session, acct: OAuthAccount) -> str | None:
    """
    Make sure we have a valid access token; refresh if needed.
    Returns access_token or None if refresh failed.
    """
    if not acct.access_token:
        return None
    if _needs_refresh(acct) and acct.refresh_token:
        data = refresh_access_token(acct.refresh_token)
        if not data:
            return None
        acct.access_token = data.get("access_token")
        acct.refresh_token = data.get("refresh_token") or acct.refresh_token
        acct.expires_at = int(data.get("expires_at") or acct.expires_at or 0)
        db.add(acct)
        db.commit()
        db.refresh(acct)
    return acct.access_token


def _strava_type_to_sport(t: str) -> str:
    """
    Map Strava's activity 'type' to our 'sport' string.
    """
    t = (t or "").lower()
    if t in ("ride", "virtualride", "gravelride", "mountainbikeride"):
        return "cycling"
    if t in ("run", "trailrun"):
        return "run"
    if t in ("swim",):
        return "swim"
    if t in ("weighttraining", "weights", "crosstraining", "workout"):
        return "strength"
    return t or "other"


def _parse_start_time(s: str) -> datetime:
    # Strava returns ISO8601 with Z; make it fromisoformat-friendly
    if not s:
        return datetime.utcnow()
    if s.endswith("Z"):
        s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s)


def _pull_page(token: str, page: int, per_page: int, after_epoch: int | None) -> list[dict[str, Any]]:
    headers = {"Authorization": f"Bearer {token}"}
    params: dict[str, Any] = {"page": page, "per_page": per_page}
    if after_epoch:
        params["after"] = after_epoch
    url = f"{STRAVA_API_BASE}/athlete/activities"
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Strava API error {resp.status_code}: {resp.text[:200]}")
    return resp.json()  # list of activities


def _upsert_activity(
    db: Session,
    user_id: int,
    provider: str,
    source_id: str,
    payload: dict[str, Any],
) -> tuple[bool, bool, Activity]:
    """
    Upsert by unique key (user_id, provider, source_id).
    Returns (created, updated, activity)
    """
    act = (
        db.query(Activity)
        .filter(
            Activity.user_id == user_id,
            Activity.source_provider == provider,
            Activity.source_id == source_id,
        )
        .first()
    )

    fields = {
        "sport": payload["sport"],
        "start_time": payload["start_time"],
        "duration_s": payload["duration_s"],
        "kcal": payload.get("kcal"),
        "distance_m": payload.get("distance_m"),
    }

    if act is None:
        act = Activity(
            user_id=user_id,
            source_provider=provider,
            source_id=source_id,
            **fields,
        )
        db.add(act)
        db.commit()
        db.refresh(act)
        return True, False, act

    updated = False
    for k, v in fields.items():
        if getattr(act, k) != v:
            setattr(act, k, v)
            updated = True
    if updated:
        db.add(act)
        db.commit()
        db.refresh(act)
    return False, updated, act


def sync_strava(
    db: Session,
    user: User,
    since: date | None = None,
    max_pages: int = 10,
    per_page: int = 50,
) -> dict[str, Any]:
    """
    Pulls recent activities from Strava for the given user.

    since: optional date (inclusive). If provided, only activities after this date are pulled.
    """
    acct = db.query(OAuthAccount).filter(OAuthAccount.user_id == user.id, OAuthAccount.provider == "strava").first()
    if not acct or not acct.access_token:
        return {
            "linked": False,
            "provider": "strava",
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "pages": 0,
        }

    token = _ensure_token(db, acct)
    if not token:
        return {"linked": True, "provider": "strava", "error": "token_invalid"}

    after_epoch = None
    if since:
        after_dt = datetime(since.year, since.month, since.day)
        after_epoch = int(after_dt.timestamp())

    created = updated = skipped = 0
    pages = 0

    for page in range(1, max_pages + 1):
        items = _pull_page(token, page=page, per_page=per_page, after_epoch=after_epoch)
        pages += 1
        if not items:
            break

        for it in items:
            source_id = str(it.get("id"))
            sport = _strava_type_to_sport(it.get("type") or "")
            start_time = _parse_start_time(it.get("start_date") or it.get("start_date_local") or "")
            duration_s = int(it.get("elapsed_time") or 0)
            distance_m = float(it.get("distance") or 0.0)

            # kcal sometimes given as kilojoules (~= kcal for cycling power; rough)
            kcal = it.get("kilojoules")
            if kcal is not None:
                try:
                    kcal = int(round(float(kcal)))
                except Exception:
                    kcal = None
            else:
                kcal = None

            payload = dict(
                sport=sport,
                start_time=start_time,
                duration_s=duration_s,
                distance_m=distance_m if distance_m > 0 else None,
                kcal=kcal,
            )
            try:
                c, u, _ = _upsert_activity(db, user.id, "strava", source_id, payload)
                if c:
                    created += 1
                elif u:
                    updated += 1
                else:
                    skipped += 1
            except Exception:
                skipped += 1

        if len(items) < per_page:
            break

    return {
        "linked": True,
        "provider": "strava",
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "pages": pages,
    }
