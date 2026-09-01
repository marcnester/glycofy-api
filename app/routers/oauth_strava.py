# app/routers/oauth_strava.py
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import requests
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth_utils import get_current_user
from app.config import settings
from app.db import SessionLocal, get_db
from app.models import Activity, OAuthAccount, User
from app.observability import record_security_event
from app.rate_limit import AUTH_LIMITER, client_key

# Router for OAuth endpoints (mounted at /oauth/strava)
router = APIRouter(tags=["oauth/strava"])
# Separate router for sync endpoints (mounted at /sync/strava)
sync_router = APIRouter(tags=["sync/strava"])

STRAVA_AUTH = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN = "https://www.strava.com/oauth/token"
STRAVA_ACTIVITIES = "https://www.strava.com/api/v3/athlete/activities"
STRAVA_ACTIVITY_DETAIL = "https://www.strava.com/api/v3/activities/{id}"

DEFAULT_PROFILE_URL = "/ui/profile.html"
STATE_COOKIE_NAME = "strava_oauth_state"
UA = "glycofy-app/1.0 (+https://example.invalid)"  # simple UA for Strava API etiquette

# ---- kcal detail fetch cap per sync to protect rate limits
DETAIL_KCAL_FETCH_CAP = 60


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(os.getenv(name, str(default)))))
    except (TypeError, ValueError):
        return default


LOGIN_SYNC_FRESHNESS_MINUTES = _env_int("STRAVA_LOGIN_SYNC_FRESHNESS_MINUTES", 30, 1, 1440)
INCREMENTAL_OVERLAP_HOURS = _env_int("STRAVA_INCREMENTAL_OVERLAP_HOURS", 24, 1, 168)
INITIAL_INCREMENTAL_MONTHS = _env_int("STRAVA_INITIAL_SYNC_MONTHS", 6, 1, 36)

logger = logging.getLogger(__name__)
_LOGIN_SYNC_LOCK = threading.Lock()
_LOGIN_SYNCS_IN_FLIGHT: set[int] = set()

# ---- sport labels we normalize to "cycling-like"
CYCLING_LIKE = {"Cycling", "Cycling (Virtual)"}

# ---- MET fallback (only used when feed+detail have no calories) -------------
# Conservative values to avoid overestimation.
_MET_TABLE = {
    "Cycling": 8.5,
    "Cycling (Virtual)": 8.5,
    "Running": 10.0,
    "TrailRun": 11.0,
    "Walking": 3.5,
    "Hiking": 6.0,
    "Rowing": 7.0,
    "Elliptical": 5.0,
    "Swimming": 6.0,
    "WeightTraining": 6.0,
    "StrengthTraining": 6.0,
    "Yoga": 2.5,
    "Workout": 5.0,  # default
}


# ───────────────────────── helpers ─────────────────────────


def _now_ts() -> int:
    return int(time.time())


def _configured() -> bool:
    return bool(settings.STRAVA_CLIENT_ID and settings.STRAVA_CLIENT_SECRET and settings.STRAVA_REDIRECT_URI)


def _safe_return_path(raw: str | None) -> str | None:
    """
    Only allow same-origin paths like /ui/profile.html or /xyz.
    Reject full URLs and suspicious values.
    """
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("/") and not raw.startswith("//") and "://" not in raw:
        return raw
    return None


def _encode_state(user_id: int, return_path: str | None) -> str:
    payload = {
        "uid": user_id,
        "return": _safe_return_path(return_path),
        "exp": _now_ts() + settings.OAUTH_STATE_TTL_SECONDS,
        "nonce": secrets.token_urlsafe(24),
    }
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(settings.JWT_SECRET.encode(), body.encode(), hashlib.sha256).digest()
    sig = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{body}.{sig}"


def _decode_state(state: str) -> tuple[int, str | None]:
    """
    Decode user id (required) and optional return path from state.
    """
    try:
        body, supplied_sig = state.split(".", 1)
    except ValueError:
        raise ValueError("bad_state")
    expected = hmac.new(settings.JWT_SECRET.encode(), body.encode(), hashlib.sha256).digest()
    expected_sig = base64.urlsafe_b64encode(expected).decode().rstrip("=")
    if not hmac.compare_digest(supplied_sig, expected_sig):
        raise ValueError("bad_state")
    try:
        pad = "=" * ((4 - len(body) % 4) % 4)
        payload = json.loads(base64.urlsafe_b64decode(body + pad))
        user_id = int(payload["uid"])
        expires = int(payload["exp"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("bad_state")
    if expires <= _now_ts():
        raise ValueError("expired_state")
    return user_id, _safe_return_path(payload.get("return"))


def _authorize_url(user_id: int, return_path: str | None = None) -> str:
    state = _encode_state(user_id, _safe_return_path(return_path))
    # scope kept minimal for reading activities; extend if you later need write.
    return (
        f"{STRAVA_AUTH}"
        f"?client_id={settings.STRAVA_CLIENT_ID}"
        f"&redirect_uri={settings.STRAVA_REDIRECT_URI}"
        f"&response_type=code"
        f"&approval_prompt=auto"
        f"&scope=read,activity:read_all"
        f"&state={state}"
    )


def _upsert_strava_account(
    db: Session,
    user_id: int,
    access_token: str,
    refresh_token: str | None,
    expires_at: int | None,
    scope: str | None,
    external_athlete_id: str | None,
) -> None:
    acct: OAuthAccount | None = (
        db.query(OAuthAccount).filter(OAuthAccount.user_id == user_id, OAuthAccount.provider == "strava").first()
    )
    if acct is None:
        acct = OAuthAccount(
            user_id=user_id,
            provider="strava",
            external_athlete_id=external_athlete_id,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            scope=scope,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(acct)
    else:
        acct.external_athlete_id = external_athlete_id or acct.external_athlete_id
        acct.access_token = access_token
        if refresh_token:
            acct.refresh_token = refresh_token
        if expires_at:
            acct.expires_at = int(expires_at)
        if scope:
            acct.scope = scope
        acct.updated_at = datetime.utcnow()
    db.commit()


def _load_strava_account(db: Session, user_id: int) -> OAuthAccount | None:
    return db.query(OAuthAccount).filter(OAuthAccount.user_id == user_id, OAuthAccount.provider == "strava").first()


def _coerce_utc_naive(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
        except ValueError:
            return None
    return None


def _latest_strava_activity_at(db: Session, user_id: int) -> datetime | None:
    row = (
        db.query(Activity.start_time)
        .filter(Activity.user_id == user_id, Activity.source_provider == "strava")
        .order_by(Activity.start_time.desc())
        .first()
    )
    return row[0] if row and isinstance(row[0], datetime) else None


def _background_incremental_sync(user_id: int) -> None:
    try:
        with SessionLocal() as db:
            user = db.query(User).filter(User.id == user_id).first()
            if not user:
                return
            result = sync_strava(replace=False, months=INITIAL_INCREMENTAL_MONTHS, db=db, user=user)
            logger.info("Strava login sync completed user_id=%s result=%s", user_id, result)
    except Exception:
        logger.exception("Strava login sync failed user_id=%s", user_id)
    finally:
        with _LOGIN_SYNC_LOCK:
            _LOGIN_SYNCS_IN_FLIGHT.discard(user_id)


def schedule_strava_sync_on_login(
    *,
    background_tasks: BackgroundTasks,
    db: Session,
    user_id: int,
    freshness_minutes: int = LOGIN_SYNC_FRESHNESS_MINUTES,
) -> bool:
    """Queue a deduplicated incremental Strava sync when stored data is stale."""
    if not _configured():
        return False

    acct = _load_strava_account(db, user_id)
    if not acct or not acct.access_token:
        return False

    latest_activity = _latest_strava_activity_at(db, user_id)
    last_sync = _coerce_utc_naive(acct.updated_at)
    fresh_after = datetime.utcnow() - timedelta(minutes=max(1, freshness_minutes))
    if latest_activity is not None and last_sync is not None and last_sync >= fresh_after:
        return False

    with _LOGIN_SYNC_LOCK:
        if user_id in _LOGIN_SYNCS_IN_FLIGHT:
            return False
        _LOGIN_SYNCS_IN_FLIGHT.add(user_id)

    try:
        # A short database-backed lease prevents another app worker from
        # scheduling the same user's sync during a simultaneous login.
        acct.updated_at = datetime.utcnow()
        db.add(acct)
        db.commit()
        background_tasks.add_task(_background_incremental_sync, user_id)
        return True
    except Exception:
        db.rollback()
        with _LOGIN_SYNC_LOCK:
            _LOGIN_SYNCS_IN_FLIGHT.discard(user_id)
        logger.exception("Unable to schedule Strava login sync user_id=%s", user_id)
        return False


def _refresh_if_needed(db: Session, acct: OAuthAccount) -> str:
    """Return a valid access token, refreshing if near expiry (≤90s)."""
    if not acct.expires_at or acct.expires_at - 90 > _now_ts():
        return acct.access_token
    if not acct.refresh_token:
        return acct.access_token

    try:
        resp = requests.post(
            STRAVA_TOKEN,
            data={
                "client_id": settings.STRAVA_CLIENT_ID,
                "client_secret": settings.STRAVA_CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": acct.refresh_token,
            },
            timeout=30,
            headers={"User-Agent": UA},
        )
        if resp.status_code != 200:
            return acct.access_token
        j = resp.json()
        acct.access_token = j.get("access_token") or acct.access_token
        if j.get("refresh_token"):
            acct.refresh_token = j["refresh_token"]
        if j.get("expires_at"):
            acct.expires_at = int(j["expires_at"])
        acct.updated_at = datetime.utcnow()
        db.commit()
        return acct.access_token
    except Exception:
        return acct.access_token


def _normalize_sport(act: dict[str, Any]) -> str:
    sport = act.get("sport_type") or act.get("type") or "Workout"
    sport = sport.strip() if isinstance(sport, str) else "Workout"
    return {
        "Ride": "Cycling",
        "VirtualRide": "Cycling (Virtual)",
        "EBikeRide": "Cycling",
        "Run": "Running",
        "TrailRun": "Running",
        "Swim": "Swimming",
        "Walk": "Walking",
    }.get(sport, sport)


def _estimate_kcal_from_feed(act: dict[str, Any], sport_norm: str) -> tuple[float | None, str]:
    """
    Return (kcal, source_tag) using only list-feed data.
    source_tag in {"feed_cal", "feed_kj_cycling", "feed_kj_mech", "none"}.
    """
    # Prefer direct calories if present in the feed (rare, but possible)
    if isinstance(act.get("calories"), (int, float)):
        try:
            return float(act["calories"]), "feed_cal"
        except Exception:
            pass

    # Next try kJ → kcal fallback
    if isinstance(act.get("kilojoules"), (int, float)):
        kj = float(act["kilojoules"])
        # Cycling-like: Strava UI is ~1.0 × kJ when power is present
        if sport_norm in CYCLING_LIKE:
            return kj * 1.0, "feed_kj_cycling"
        # Generic conservative mechanical kcal (kJ / 4.184)
        return kj * 0.239, "feed_kj_mech"

    return None, "none"


def _fetch_detail_calories(token: str, activity_id: str) -> float | None:
    """
    Fetch calories from the Strava activity detail endpoint.
    Returns None if unavailable or on non-200 (other than 404).
    """
    try:
        url = STRAVA_ACTIVITY_DETAIL.format(id=activity_id)
        r = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}", "User-Agent": UA},
            timeout=20,
        )
        # 404/410 etc. → treat as no calories; avoid raising
        if r.status_code == 404:
            return None
        if r.status_code == 429:
            # surface as None; the sync loop will stop calling details when budget is spent
            return None
        if r.status_code != 200:
            return None
        j = r.json() or {}
        cal = j.get("calories")
        if isinstance(cal, (int, float)):
            return float(cal)
        return None
    except Exception:
        return None


def _estimate_kcal_from_met(sport: str, duration_s: int, weight_kg: float | None) -> float:
    """
    Final fallback: estimate kcal via MET × weight × hours.
    Used only when feed + detail produce no calories.
    """
    if not duration_s or duration_s <= 0:
        return 0.0
    met = _MET_TABLE.get(sport, _MET_TABLE["Workout"])
    wk = weight_kg if (isinstance(weight_kg, (int, float)) and weight_kg > 0) else 75.0
    hours = float(duration_s) / 3600.0
    return float(met * wk * hours)


def _map_strava_activity_basic(act: dict[str, Any]) -> dict[str, Any]:
    """
    Map Strava list feed JSON to core Activity fields WITHOUT forcing a detail call.
    kcal here is feed-derived (direct cal if present, otherwise kJ fallback);
    the sync loop may refine it with detail kcal for a bounded number of rows,
    and finally by MET fallback when still zero.
    """
    sport_norm = _normalize_sport(act)

    kcal_val, _ = _estimate_kcal_from_feed(act, sport_norm)
    if kcal_val is None:
        kcal_val = 0.0

    distance_m = float(act.get("distance") or 0.0)
    try:
        duration_s = int(act.get("elapsed_time") or 0)
    except Exception:
        duration_s = 0

    start_iso = act.get("start_date") or act.get("start_date_local")
    if start_iso and isinstance(start_iso, str):
        try:
            dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00")).astimezone(UTC)
            start_time = dt.replace(tzinfo=None)
        except Exception:
            start_time = datetime.utcnow()
    else:
        start_time = datetime.utcnow()

    return {
        "provider": "strava",  # ← REQUIRED (NOT NULL)
        "sport": sport_norm,
        "start_time": start_time,
        "duration_s": duration_s,
        "kcal": float(kcal_val),
        "distance_m": distance_m,
        "source_provider": "strava",
        "source_id": str(act.get("id") or ""),
        "created_at": datetime.utcnow(),
    }


def _fetch_all_strava_activities(token: str, after_ts: int | None = None) -> list[dict[str, Any]]:
    """Page through Strava activities; return a list of raw activity dicts."""
    headers = {"Authorization": f"Bearer {token}", "User-Agent": UA}
    per_page = 200
    page = 1
    all_items: list[dict[str, Any]] = []

    while True:
        params = {"per_page": per_page, "page": page}
        if after_ts:
            params["after"] = after_ts
        r = requests.get(STRAVA_ACTIVITIES, headers=headers, params=params, timeout=30)
        if r.status_code == 401:
            raise HTTPException(status_code=401, detail="Strava token unauthorized")
        if r.status_code == 429:
            # Respect rate limits; surface clearly to caller
            raise HTTPException(status_code=429, detail="Strava rate limit hit")
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Strava API error {r.status_code}")
        batch = r.json() or []
        if not batch:
            break
        all_items.extend(batch)
        if len(batch) < per_page:
            break
        page += 1

    return all_items


# ───────────────────────── OAuth routes (require auth) ─────────────────────────


@router.get("/status")
def strava_status(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    configured = _configured()
    acct = _load_strava_account(db, user.id)
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


@router.get("/start-url")
def strava_start_url(
    request: Request,
    return_path: str | None = Query(default=None, alias="return"),
    user: User = Depends(get_current_user),
):
    if not _configured():
        raise HTTPException(status_code=400, detail="Strava is not configured on this server.")
    AUTH_LIMITER.check(
        f"oauth-strava-start:ip:{client_key(request)}",
        maximum=settings.OAUTH_RATE_LIMIT_PER_15_MINUTES,
        window_seconds=900,
    )
    url = _authorize_url(user.id, _safe_return_path(return_path))
    state = url.rsplit("&state=", 1)[1]
    response = JSONResponse({"authorize_url": url})
    response.set_cookie(
        STATE_COOKIE_NAME,
        state,
        max_age=settings.OAUTH_STATE_TTL_SECONDS,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/oauth/strava/callback",
    )
    return response


@router.get("/start")
def strava_start(
    request: Request,
    return_path: str | None = Query(default=None, alias="return"),
    user: User = Depends(get_current_user),
):
    if not _configured():
        raise HTTPException(status_code=400, detail="Strava is not configured on this server.")
    AUTH_LIMITER.check(
        f"oauth-strava-start:ip:{client_key(request)}",
        maximum=settings.OAUTH_RATE_LIMIT_PER_15_MINUTES,
        window_seconds=900,
    )
    url = _authorize_url(user.id, _safe_return_path(return_path))
    state = url.rsplit("&state=", 1)[1]
    response = RedirectResponse(url=url, status_code=302)
    response.set_cookie(
        STATE_COOKIE_NAME,
        state,
        max_age=settings.OAUTH_STATE_TTL_SECONDS,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/oauth/strava/callback",
    )
    return response


# ───────────────────────── PUBLIC callback (no auth) ─────────────────────────


@router.get("/callback")
def strava_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    scope: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    if error:
        record_security_event(db, request, "oauth_strava_callback", "provider_denied", severity="warning")
        return RedirectResponse(url=f"{DEFAULT_PROFILE_URL}?linked_error=provider_denied", status_code=302)

    if not _configured():
        return RedirectResponse(url=f"{DEFAULT_PROFILE_URL}?linked_error=server_not_configured", status_code=302)

    if not code or not state:
        return RedirectResponse(url=f"{DEFAULT_PROFILE_URL}?linked_error=missing_code_or_state", status_code=302)

    try:
        AUTH_LIMITER.check(
            f"oauth-strava-callback:ip:{client_key(request)}",
            maximum=settings.OAUTH_RATE_LIMIT_PER_15_MINUTES,
            window_seconds=900,
        )
    except HTTPException:
        record_security_event(db, request, "oauth_strava_rate_limited", "denied", severity="alert")
        raise

    state_cookie = request.cookies.get(STATE_COOKIE_NAME, "")
    if not state_cookie or not hmac.compare_digest(state_cookie, state):
        record_security_event(db, request, "oauth_strava_callback", "invalid_state", severity="alert")
        return RedirectResponse(url=f"{DEFAULT_PROFILE_URL}?linked_error=bad_state", status_code=302)

    try:
        user_id, return_path = _decode_state(state)
    except Exception:
        record_security_event(db, request, "oauth_strava_callback", "invalid_state", severity="alert")
        return RedirectResponse(url=f"{DEFAULT_PROFILE_URL}?linked_error=bad_state", status_code=302)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return RedirectResponse(url=f"{DEFAULT_PROFILE_URL}?linked_error=user_not_found", status_code=302)

    try:
        resp = requests.post(
            STRAVA_TOKEN,
            data={
                "client_id": settings.STRAVA_CLIENT_ID,
                "client_secret": settings.STRAVA_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": settings.STRAVA_REDIRECT_URI,
            },
            timeout=30,
            headers={"User-Agent": UA},
        )
    except Exception:
        return RedirectResponse(url=f"{DEFAULT_PROFILE_URL}?linked_error=request_failed", status_code=302)

    if resp.status_code != 200:
        return RedirectResponse(
            url=f"{DEFAULT_PROFILE_URL}?linked_error=token_exchange_{resp.status_code}",
            status_code=302,
        )

    data = resp.json()
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    expires_at = data.get("expires_at")
    athlete = data.get("athlete") or {}
    external_athlete_id = str(athlete.get("id") or "") if isinstance(athlete, dict) else None

    if not access_token:
        return RedirectResponse(url=f"{DEFAULT_PROFILE_URL}?linked_error=no_access_token", status_code=302)

    # NEW: light enrichment from Strava athlete profile (defensive, optional)
    dirty = False
    try:
        # display_name from first/last name if empty
        disp = getattr(user, "display_name", None)
        if not disp:
            first = (athlete.get("firstname") or "").strip()
            last = (athlete.get("lastname") or "").strip()
            name = (first + " " + last).strip()
            if name:
                user.display_name = name
                dirty = True
    except Exception:
        pass

    try:
        # units from measurement_preference if empty
        units = getattr(user, "units", None)
        if not units:
            pref = (athlete.get("measurement_preference") or "").lower().strip()
            if pref in ("feet", "foot"):
                user.units = "US"
                dirty = True
            elif pref in ("meters", "metres", "metric"):
                user.units = "Metric"
                dirty = True
    except Exception:
        pass

    if dirty:
        db.add(user)
        db.commit()
        db.refresh(user)

    _upsert_strava_account(
        db=db,
        user_id=user_id,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=int(expires_at) if expires_at else None,
        scope=scope or data.get("scope"),
        external_athlete_id=external_athlete_id,
    )

    dest = return_path or DEFAULT_PROFILE_URL
    sep = "&" if "?" in dest else "?"
    response = RedirectResponse(url=f"{dest}{sep}linked=strava", status_code=302)
    record_security_event(db, request, "oauth_strava_link", "success", user_id=user.id)
    response.delete_cookie(STATE_COOKIE_NAME, path="/oauth/strava/callback", samesite="lax")
    return response


# ───────────────────────── Sync routes (require auth) ─────────────────────────


@sync_router.post("")
def sync_strava(
    replace: bool = Query(default=False),
    months: int = Query(default=6, ge=1, le=36, description="Lookback window if not replacing"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Import activities from Strava into our activities table.

    - `replace=true` deletes existing rows for this user with source_provider='strava'
      before importing everything available.
    - Otherwise, fetch a bounded history and upsert by (user_id, source_provider, source_id).
    - Calories priority: feed.calories → kJ fallback → detail endpoint → MET estimate.
    """
    if not _configured():
        raise HTTPException(status_code=400, detail="Strava not configured")

    acct = _load_strava_account(db, user.id)
    if not acct or not acct.access_token:
        raise HTTPException(status_code=400, detail="Strava not linked")

    token = _refresh_if_needed(db, acct)

    after_ts: int | None = None
    if not replace:
        latest_activity = _latest_strava_activity_at(db, user.id)
        if latest_activity is not None:
            # Include a small overlap so recently edited activities are
            # refreshed without rescanning the user's full history.
            dt = latest_activity - timedelta(hours=INCREMENTAL_OVERLAP_HOURS)
        else:
            dt = datetime.utcnow() - timedelta(days=30 * months)
        after_ts = int(dt.replace(tzinfo=UTC).timestamp())

    deleted = 0
    if replace:
        deleted = (
            db.query(Activity)
            .filter(
                Activity.user_id == user.id,
                Activity.source_provider == "strava",
            )
            .delete(synchronize_session=False)
        )
        db.commit()

    try:
        raw_items = _fetch_all_strava_activities(token, after_ts=after_ts)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch from Strava: {e}")

    created = 0
    updated = 0

    # budget for detail calls to copy Strava's own Calories when absent in the feed
    detail_budget = DETAIL_KCAL_FETCH_CAP

    # Try to read weight (optional; used only for MET fallback)
    weight_kg: float | None = None
    for attr in ("weight", "weight_kg", "body_weight_kg"):
        try:
            v = getattr(user, attr, None)
            if isinstance(v, (int, float)) and v > 0:
                weight_kg = float(v)
                break
        except Exception:
            pass

    for act in raw_items:
        # 1) Map the feed item
        mapped = _map_strava_activity_basic(act)
        if not mapped["source_id"]:
            continue

        # 2) If feed had NO calories and we still have some budget, try detail endpoint
        feed_had_cal = isinstance(act.get("calories"), (int, float))
        if (not feed_had_cal) and detail_budget > 0 and (mapped["kcal"] <= 0):
            det_cal = _fetch_detail_calories(token, mapped["source_id"])
            if isinstance(det_cal, (int, float)):
                mapped["kcal"] = float(det_cal)
            detail_budget -= 1

        # 3) If still zero, estimate via MET fallback (duration × MET × weight)
        if mapped["kcal"] <= 0:
            mapped["kcal"] = _estimate_kcal_from_met(
                sport=mapped["sport"],
                duration_s=mapped["duration_s"],
                weight_kg=weight_kg,
            )

        # 4) Upsert into DB
        existing: Activity | None = (
            db.query(Activity)
            .filter(
                Activity.user_id == user.id,
                Activity.source_provider == "strava",
                Activity.source_id == mapped["source_id"],
            )
            .first()
        )

        if existing:
            # Back-fill provider if missing and update other fields
            if not getattr(existing, "provider", None):
                existing.provider = "strava"
            existing.sport = mapped["sport"]
            existing.start_time = mapped["start_time"]
            existing.duration_s = mapped["duration_s"]
            existing.kcal = mapped["kcal"]
            existing.distance_m = mapped["distance_m"]
            existing.created_at = existing.created_at or mapped["created_at"]
            updated += 1
        else:
            row = Activity(
                user_id=user.id,
                provider="strava",  # ← REQUIRED (NOT NULL)
                sport=mapped["sport"],
                start_time=mapped["start_time"],
                duration_s=mapped["duration_s"],
                kcal=mapped["kcal"],
                distance_m=mapped["distance_m"],
                source_provider="strava",
                source_id=mapped["source_id"],
                created_at=mapped["created_at"],
            )
            db.add(row)
            created += 1

    acct.updated_at = datetime.utcnow()
    db.add(acct)
    db.commit()

    return {
        "status": "ok",
        "replace": replace,
        "deleted": deleted,
        "fetched": len(raw_items),
        "created": created,
        "updated": updated,
        "detail_kcal_fetches": DETAIL_KCAL_FETCH_CAP - detail_budget,
    }
