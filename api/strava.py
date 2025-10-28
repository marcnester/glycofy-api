# api/strava.py
import os
import time
from typing import Any

import httpx
from dotenv import find_dotenv, load_dotenv
from sqlalchemy.orm import Session

from .models import Activity, StravaToken

# Load .env reliably for this module
load_dotenv(find_dotenv(), override=False)

AUTH_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
API_BASE = "https://www.strava.com/api/v3"


# ---------- Env helpers ----------
def _mask(s: str | None) -> str:
    if not s:
        return ""
    if len(s) <= 6:
        return "*" * len(s)
    return s[:3] + "*" * (len(s) - 6) + s[-3:]


def _cfg() -> dict[str, str]:
    """Fetch env dynamically every call (avoids stale values)."""
    return {
        "client_id": os.getenv("STRAVA_CLIENT_ID", "").strip(),
        "client_secret": os.getenv("STRAVA_CLIENT_SECRET", "").strip(),
        "redirect_uri": os.getenv("STRAVA_REDIRECT_URI", "").strip(),
    }


def effective_config() -> dict[str, Any]:
    c = _cfg()
    return {
        "client_id": c["client_id"],
        "has_secret": bool(c["client_secret"]),
        "secret_preview": _mask(c["client_secret"]),
        "redirect_uri": c["redirect_uri"],
        "configured": bool(c["client_id"] and c["client_secret"] and c["redirect_uri"]),
    }


def assert_strava_env():
    c = _cfg()
    problems = []
    if not c["client_id"]:
        problems.append("STRAVA_CLIENT_ID missing")
    if not c["client_secret"]:
        problems.append("STRAVA_CLIENT_SECRET missing")
    if not c["redirect_uri"]:
        problems.append("STRAVA_REDIRECT_URI missing")
    if problems:
        raise ValueError("Strava config error: " + "; ".join(problems))
    # Client ID must be numeric for Strava
    try:
        int(c["client_id"])
    except Exception:
        raise ValueError("STRAVA_CLIENT_ID must be the numeric ID from Strava (not the secret).")


# ---------- OAuth URLs / token exchange ----------
def authorize_url(state: str = "glyco"):
    assert_strava_env()
    c = _cfg()
    scopes = "read,activity:read_all"
    return (
        f"{AUTH_URL}?client_id={c['client_id']}"
        f"&response_type=code&redirect_uri={c['redirect_uri']}"
        f"&approval_prompt=auto&scope={scopes}&state={state}"
    )


async def exchange_code(code: str) -> dict[str, Any]:
    assert_strava_env()
    c = _cfg()
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            TOKEN_URL,
            data={
                "client_id": c["client_id"],
                "client_secret": c["client_secret"],
                "code": code,
                "grant_type": "authorization_code",
            },
        )
        r.raise_for_status()
        return r.json()


async def refresh_access_token(db: Session, tok: StravaToken) -> StravaToken:
    assert_strava_env()
    c = _cfg()
    now = int(time.time())
    if tok.expires_at and tok.expires_at - now > 60:
        return tok
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            TOKEN_URL,
            data={
                "client_id": c["client_id"],
                "client_secret": c["client_secret"],
                "grant_type": "refresh_token",
                "refresh_token": tok.refresh_token,
            },
        )
        r.raise_for_status()
        data = r.json()
        tok.access_token = data["access_token"]
        tok.refresh_token = data.get("refresh_token", tok.refresh_token)
        tok.expires_at = int(data.get("expires_at", now + 3600))
        db.add(tok)
        db.commit()
        db.refresh(tok)
        return tok


# ---------- Activity fetch + upsert ----------
async def fetch_activities(
    db: Session,
    tok: StravaToken,
    after_ts: int | None = None,
    per_page: int = 100,
    max_pages: int = 10,
) -> list[dict[str, Any]]:
    tok = await refresh_access_token(db, tok)
    headers = {"Authorization": f"Bearer {tok.access_token}"}
    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=30) as client:
        page = 1
        while page <= max_pages:
            params = {"per_page": per_page, "page": page}
            if after_ts:
                params["after"] = after_ts
            r = await client.get(f"{API_BASE}/athlete/activities", headers=headers, params=params)
            r.raise_for_status()
            items = r.json()
            if not items:
                break
            results.extend(items)
            page += 1
    return results


def _kcal_from_strava_obj(o: dict[str, Any]) -> int:
    if isinstance(o.get("calories"), (int, float)):
        return int(round(o["calories"]))
    if isinstance(o.get("kilojoules"), (int, float)):
        # 1 kJ ≈ 0.239 kcal
        return int(round(float(o["kilojoules"]) * 0.239))
    moving = int(o.get("moving_time") or 0)
    return int(round((moving / 60.0) * 7.0)) if moving > 0 else 0


def _normalize_sport(o: dict[str, Any]) -> str:
    raw = (o.get("sport_type") or o.get("type") or "").strip()
    raw_key = raw.replace(" ", "").lower()
    mapping = {
        "ride": "Cycling",
        "virtualride": "Cycling (Virtual)",
        "ebikeride": "E-Bike",
        "mountainbikeride": "Cycling (MTB)",
        "gravelride": "Cycling (Gravel)",
        "run": "Running",
        "trailrun": "Running (Trail)",
        "walk": "Walking",
        "hike": "Hiking",
        "swim": "Swimming",
        "rowing": "Rowing",
        "alpineski": "Skiing (Alpine)",
        "nordicski": "Skiing (Nordic)",
        "snowboard": "Snowboard",
        "workout": "Workout",
        "weighttraining": "Strength",
        "yoga": "Yoga",
        "pilates": "Pilates",
        "elliptical": "Elliptical",
        "wheelchair": "Wheelchair",
        "iceskate": "Ice Skate",
        "rollerski": "Roller Ski",
        "kayaking": "Kayaking",
        "canoeing": "Canoeing",
        "surfing": "Surfing",
        "kitesurf": "Kitesurf",
        "windsurf": "Windsurf",
        "golf": "Golf",
        "rockclimbing": "Climbing",
    }
    return mapping.get(raw_key, raw or "Workout")


def _title_from(o: dict[str, Any], sport: str) -> str:
    """Choose a nicer title when Strava's name is generic."""
    name = (o.get("name") or "").strip()
    # Consider these "generic"
    generic = {
        sport.lower(),
        "workout",
        "morning run",
        "evening run",
        "morning ride",
        "evening ride",
        "",
    }
    is_generic = name.lower() in generic

    # Try to compose something helpful
    dist_m = o.get("distance")
    moving = int(o.get("moving_time") or 0)
    pieces = [sport]
    if isinstance(dist_m, (int, float)) and dist_m > 0:
        km = dist_m / 1000.0
        # 1 decimal if < 100km, else round.
        km_txt = f"{km:.1f}" if km < 100 else f"{round(km)}"
        pieces.append(f"{km_txt} km")
    elif moving > 0:
        pieces.append(f"{round(moving/60)} min")

    composed = " — ".join(pieces)
    return (composed if is_generic else name) or composed or name or sport or "Workout"


def _to_int(n: Any) -> int:
    if n is None:
        return 0
    if isinstance(n, bool):
        return int(n)
    try:
        return int(round(float(n)))
    except Exception:
        return 0


def upsert_activities(db: Session, user_sub: str, raw: list[dict[str, Any]]) -> int:
    inserted = 0
    for o in raw:
        try:
            sid = o["id"]
        except KeyError:
            continue

        try:
            act = db.query(Activity).filter_by(user_sub=user_sub, strava_id=sid).one_or_none()
            sport = _normalize_sport(o)
            kcal = _kcal_from_strava_obj(o)
            start_iso = o.get("start_date") or o.get("start_date_local") or ""
            moving = _to_int(o.get("moving_time"))
            distance_m = _to_int(o.get("distance"))
            title = _title_from(o, sport)

            if act is None:
                act = Activity(
                    user_sub=user_sub,
                    strava_id=sid,
                    name=title,
                    type=sport,
                    start_time=start_iso,
                    duration_sec=moving,
                    distance_m=distance_m,
                    kcal=kcal,
                )
                db.add(act)
                inserted += 1
            else:
                # Always improve labels if we can (fixes old "Workout" rows)
                if not act.type or act.type.lower() == "workout" or sport.lower() != "workout":
                    act.type = sport or act.type or "Workout"

                # If the stored name is generic (e.g., "Workout") and we have a better one, replace it.
                if not act.name or act.name.strip().lower() in {
                    "",
                    "workout",
                    act.type.strip().lower(),
                }:
                    act.name = title

                # Update the rest of metrics
                if start_iso:
                    act.start_time = start_iso
                act.duration_sec = moving if moving is not None else act.duration_sec
                act.distance_m = distance_m if distance_m is not None else act.distance_m
                act.kcal = kcal

        except Exception:
            # Skip broken items but continue overall
            continue

    db.commit()
    return inserted
