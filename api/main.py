# api/main.py
import logging
import os
import sqlite3
import time
import traceback
import uuid
from typing import Any

import jwt
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ───────────────────────────────────────────────
# Env / Config
# ───────────────────────────────────────────────
API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8090"))
DB_URL = os.getenv("DATABASE_URL", "sqlite:///./glycofy.db")

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret")
JWT_ALG = os.getenv("JWT_ALGORITHM", "HS256")
JWT_ISS = os.getenv("JWT_ISS", "glyco.local")
JWT_AUD = os.getenv("JWT_AUD", "glyco.web")
ID_COOKIE_NAME = os.getenv("ID_COOKIE_NAME", "id_token")

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", f"http://{API_HOST}:{API_PORT}")
SERVE_UI_FROM_API = os.getenv("SERVE_UI_FROM_API", "true").lower() == "true"

STRAVA_CLIENT_ID = os.getenv("STRAVA_CLIENT_ID", "")
STRAVA_REDIRECT_URI = os.getenv("STRAVA_REDIRECT_URI", "")


def _sqlite_path_from_url(url: str) -> str:
    if url.startswith("sqlite:///"):
        return url.replace("sqlite:///", "", 1)
    if url.startswith("sqlite://"):
        return url.replace("sqlite://", "", 1)
    raise RuntimeError("DATABASE_URL must be sqlite:// for local dev")


DB_PATH = _sqlite_path_from_url(DB_URL)


# ───────────────────────────────────────────────
# DB bootstrap (SQLite)
# ───────────────────────────────────────────────
def ensure_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS activities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_sub TEXT NOT NULL,
        strava_id INTEGER,
        name TEXT,
        type TEXT,
        start_time TEXT,     -- ISO 8601
        duration_sec INTEGER,
        distance_m REAL,
        kcal INTEGER
    );
    """
    )
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS user_prefs (
        user_sub   TEXT PRIMARY KEY,
        name       TEXT,
        timezone   TEXT,
        diet_pref  TEXT,
        updated_at INTEGER
    );
    """
    )
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS strava_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_sub TEXT NOT NULL,
        access_token TEXT,
        refresh_token TEXT,
        expires_at INTEGER,
        athlete_id INTEGER,
        scope TEXT,
        updated_at INTEGER
    );
    """
    )
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS plan_locks (
        user_sub TEXT NOT NULL,
        date     TEXT NOT NULL,
        locked   INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (user_sub, date)
    );
    """
    )
    con.commit()
    con.close()


ensure_db()

# ───────────────────────────────────────────────
# Logging
# ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("glycofy")

# ───────────────────────────────────────────────
# App
# ───────────────────────────────────────────────
app = FastAPI(title="Glycofy API", version="1.0.0")

# CORS (keeps your current origins)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[PUBLIC_BASE_URL, "http://127.0.0.1:8090", "http://localhost:8090"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ───────────────────────────────────────────────
# Security headers middleware
# ───────────────────────────────────────────────
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    # Safe defaults — avoid breaking your UI
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    # Minimal CSP to reduce risk without blocking your static UI
    response.headers.setdefault("Content-Security-Policy", "default-src 'self' 'unsafe-inline' data: blob:")
    return response


# ───────────────────────────────────────────────
# Correlation ID + access log middleware
# ───────────────────────────────────────────────
@app.middleware("http")
async def correlation_and_access_log(request: Request, call_next):
    cid = request.headers.get("x-correlation-id") or str(uuid.uuid4())
    request.state.correlation_id = cid
    start = time.perf_counter()
    try:
        response = await call_next(request)
        status = response.status_code
    except Exception as exc:
        # Let the global handler format the response; still log timing here
        status = 500
        raise exc
    finally:
        duration_ms = int((time.perf_counter() - start) * 1000)
        log.info(
            "access",
            extra={
                "cid": cid,
                "method": request.method,
                "path": request.url.path,
                "status": status,
                "dur_ms": duration_ms,
                "client": request.client.host if request.client else None,
            },
        )
    response.headers["x-correlation-id"] = cid
    return response


# ───────────────────────────────────────────────
# Global error handler (never leak internals)
# ───────────────────────────────────────────────
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    cid = getattr(request.state, "correlation_id", None)
    log.error(
        "unhandled_error",
        extra={
            "cid": cid,
            "path": request.url.path,
            "error": repr(exc),
            "trace": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        },
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "Something went wrong. Please try again.",
            }
        },
    )


# ───────────────────────────────────────────────
# Auth helpers
# ───────────────────────────────────────────────
def _decode_cookie(request: Request) -> dict[str, Any]:
    tok = request.cookies.get(ID_COOKIE_NAME)
    if not tok:
        raise HTTPException(status_code=401, detail="missing_token")
    try:
        claims = jwt.decode(tok, JWT_SECRET, algorithms=[JWT_ALG], issuer=JWT_ISS, audience=JWT_AUD)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"invalid_token: {e}")
    if "sub" not in claims:
        raise HTTPException(status_code=401, detail="invalid_token: missing sub")
    return claims


# ───────────────────────────────────────────────
# Schemas
# ───────────────────────────────────────────────
class LoginRequest(BaseModel):
    email: str
    password: str


class UserUpdate(BaseModel):
    name: str | None = None
    timezone: str | None = None
    diet_pref: str | None = None


class ActivityOut(BaseModel):
    id: str
    name: str
    type: str
    start_time: str | None
    duration_sec: int | None
    distance_m: int | None
    kcal: int | None


class ActivitiesResponse(BaseModel):
    items: list[ActivityOut]
    total: int
    page: int
    page_size: int


# ───────────────────────────────────────────────
# Health endpoints
# ───────────────────────────────────────────────
@app.get("/health/liveness", tags=["health"])
def liveness():
    return {"status": "ok"}


@app.get("/health/readiness", tags=["health"])
def readiness():
    # Minimal DB touch: ensure file is reachable
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("SELECT 1")
        con.close()
        return {"status": "ready"}
    except Exception:
        # Don't leak details
        raise HTTPException(status_code=503, detail="not_ready")


# ───────────────────────────────────────────────
# Auth endpoints (cookie session)
# ───────────────────────────────────────────────
@app.post("/auth/login")
def login(req: LoginRequest, response: Response):
    # MVP: accept any credentials; set 60-min cookie
    now = int(time.time())
    claims = {
        "sub": "user_123",
        "email": req.email,
        "name": "Marc Nester",
        "roles": ["user"],
        "iat": now,
        "nbf": now,
        "iss": JWT_ISS,
        "aud": JWT_AUD,
        "exp": now + 60 * 60,
    }
    token = jwt.encode(claims, JWT_SECRET, algorithm=JWT_ALG)
    response.set_cookie(
        ID_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=False,  # set True behind HTTPS
        path="/",
        max_age=60 * 60,
    )
    return {"ok": True, "cookie_set": True}


@app.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(ID_COOKIE_NAME, path="/", httponly=True, samesite="lax", secure=False)
    return {"ok": True}


# ───────────────────────────────────────────────
# Users (GET/PUT /users/me)
# ───────────────────────────────────────────────
@app.get("/users/me")
def users_me(request: Request):
    u = _decode_cookie(request)
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT name, timezone, diet_pref FROM user_prefs WHERE user_sub=?", (u["sub"],))
    row = cur.fetchone()
    con.close()

    name = (row[0] if row and row[0] else u.get("name")) or (u.get("email") or "user").split("@")[0]
    timezone = row[1] if row and row[1] else "America/Los_Angeles"
    diet = row[2] if row and row[2] else "omnivore"

    return {
        "sub": u["sub"],
        "email": u.get("email", ""),
        "name": name,
        "timezone": timezone,
        "diet_pref": diet,
        "roles": u.get("roles", []),
    }


@app.put("/users/me")
def update_users_me(update: UserUpdate, request: Request):
    u = _decode_cookie(request)
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT name, timezone, diet_pref FROM user_prefs WHERE user_sub=?", (u["sub"],))
    row = cur.fetchone()

    name = update.name if update.name is not None else (row[0] if row else None)
    tz = update.timezone if update.timezone is not None else (row[1] if row else None)
    diet = update.diet_pref if update.diet_pref is not None else (row[2] if row else None)

    now = int(time.time())
    if row is None:
        cur.execute(
            "INSERT INTO user_prefs (user_sub, name, timezone, diet_pref, updated_at) VALUES (?, ?, ?, ?, ?)",
            (u["sub"], name, tz, diet, now),
        )
    else:
        cur.execute(
            "UPDATE user_prefs SET name=?, timezone=?, diet_pref=?, updated_at=? WHERE user_sub=?",
            (name, tz, diet, now, u["sub"]),
        )
    con.commit()
    con.close()

    return {
        "sub": u["sub"],
        "email": u.get("email", ""),
        "name": name or (u.get("name") or (u.get("email") or "user").split("@")[0]),
        "timezone": tz or "America/Los_Angeles",
        "diet_pref": diet or "omnivore",
        "roles": u.get("roles", []),
    }


# ───────────────────────────────────────────────
# Activities (paged list)
# ───────────────────────────────────────────────
@app.get("/activities", response_model=ActivitiesResponse)
def activities(request: Request, page: int = 1, page_size: int = 25):
    u = _decode_cookie(request)
    page = max(1, page)
    page_size = max(1, min(page_size, 200))

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    cur.execute("SELECT COUNT(*) FROM activities WHERE user_sub=?", (u["sub"],))
    total = int(cur.fetchone()[0] or 0)

    cur.execute(
        """
        SELECT id, name, type, start_time, duration_sec, distance_m, kcal
        FROM activities
        WHERE user_sub=?
        ORDER BY start_time DESC
        LIMIT ? OFFSET ?;
    """,
        (u["sub"], page_size, (page - 1) * page_size),
    )
    rows = cur.fetchall()
    con.close()

    items: list[ActivityOut] = []
    for r in rows:
        items.append(
            ActivityOut(
                id=str(r[0]),
                name=r[1] or "workout",
                type=r[2] or "workout",
                start_time=r[3],
                duration_sec=int(r[4] or 0) if r[4] is not None else 0,
                distance_m=int((r[5] or 0) // 1) if r[5] is not None else 0,
                kcal=int(r[6] or 0) if r[6] is not None else 0,
            )
        )

    return ActivitiesResponse(items=items, total=total, page=page, page_size=page_size)


# ───────────────────────────────────────────────
# Summary/Aggregate for Activities page (donut, totals, table)
# ───────────────────────────────────────────────
def _substr_date(col: str = "start_time") -> str:
    # first 10 chars of ISO date
    return "substr(%s,1,10)" % col


def _range_clause() -> str:
    return f"{_substr_date()} >= ? AND {_substr_date()} <= ?"


def _aggregate_summary(user_sub: str, date_from: str, date_to: str) -> dict[str, Any]:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # totals
    cur.execute(
        f"SELECT COALESCE(SUM(kcal),0), COUNT(*) FROM activities WHERE user_sub=? AND {_range_clause()}",
        (user_sub, date_from, date_to),
    )
    total_kcal, activity_count = cur.fetchone()
    total_kcal = int(total_kcal or 0)
    activity_count = int(activity_count or 0)

    # daily totals
    cur.execute(
        f"""
        SELECT {_substr_date()} AS d, COALESCE(SUM(kcal),0)
        FROM activities
        WHERE user_sub=? AND {_range_clause()}
        GROUP BY d
        ORDER BY d ASC
    """,
        (user_sub, date_from, date_to),
    )
    daily_rows = cur.fetchall()
    daily_map = {d: int(k) for d, k in daily_rows}

    # by sport overall
    cur.execute(
        f"""
        SELECT COALESCE(type, 'Workout') AS sport, COALESCE(SUM(kcal),0)
        FROM activities
        WHERE user_sub=? AND {_range_clause()}
        GROUP BY sport
        ORDER BY 2 DESC
    """,
        (user_sub, date_from, date_to),
    )
    sport_rows = cur.fetchall()
    totals_by_sport = [{"sport": s or "Workout", "kcal": int(k or 0)} for s, k in sport_rows]

    # by sport per day
    cur.execute(
        f"""
        SELECT {_substr_date()} AS d, COALESCE(type,'Workout') AS sport, COALESCE(SUM(kcal),0)
        FROM activities
        WHERE user_sub=? AND {_range_clause()}
        GROUP BY d, sport
        ORDER BY d ASC, sport ASC
    """,
        (user_sub, date_from, date_to),
    )
    per_day: dict[str, dict[str, int]] = {}
    for d, s, k in cur.fetchall():
        per_day.setdefault(d, {})[s or "Workout"] = int(k or 0)

    con.close()

    # build days list covering the whole range
    from_dt = date_from
    to_dt = date_to

    def _dates_between(a: str, b: str):
        from datetime import date, timedelta

        y1, m1, d1 = (int(x) for x in a.split("-"))
        y2, m2, d2 = (int(x) for x in b.split("-"))
        start = date(y1, m1, d1)
        end = date(y2, m2, d2)
        i = start
        while i <= end:
            yield i.isoformat()
            i += timedelta(days=1)

    days = []
    for d in _dates_between(from_dt, to_dt):
        by_sport = per_day.get(d, {})
        parts = [f"{k}: {v} kcal" for k, v in sorted(by_sport.items(), key=lambda kv: -kv[1])]
        days.append(
            {
                "date": d,
                "training_kcal": daily_map.get(d, 0),
                "planned_kcal": 0,
                "by_sport": by_sport,
                "by_sport_text": "; ".join(parts) if parts else "",
            }
        )

    return {
        "total_training_kcal": total_kcal,
        "total_planned_kcal": 0,
        "activity_count": activity_count,
        "days": days,
        "totals_by_sport": totals_by_sport,
    }


@app.get("/summary/range")
def summary_range(
    request: Request,
    frm: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    diet_pref: str | None = Query(None),
):
    """Aggregate calories/activities for a date range (inclusive)."""
    u = _decode_cookie(request)
    if not frm or not to:
        raise HTTPException(status_code=400, detail="missing from/to")
    return _aggregate_summary(u["sub"], frm, to)


# Alias to support any older code that might call /summary
@app.get("/summary")
def summary_alias(
    request: Request,
    frm: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    diet_pref: str | None = Query(None),
):
    u = _decode_cookie(request)
    if not frm or not to:
        raise HTTPException(status_code=400, detail="missing from/to")
    return _aggregate_summary(u["sub"], frm, to)


# ───────────────────────────────────────────────
# Strava endpoints (UI-safe)
# ───────────────────────────────────────────────
@app.get("/oauth/strava/status")
def strava_status(_: Request):
    # Show "Connected" if ANY token exists (helps if your token belongs to old sub)
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM strava_tokens")
    cnt = int(cur.fetchone()[0] or 0)
    cur.execute("SELECT MIN(expires_at) FROM strava_tokens")
    exp = cur.fetchone()[0]
    con.close()
    return {"connected": cnt > 0, "expires_at": exp}


@app.get("/oauth/strava/start")
def strava_start():
    if not STRAVA_CLIENT_ID or not STRAVA_REDIRECT_URI:
        raise HTTPException(status_code=500, detail="strava_not_configured")
    url = (
        "https://www.strava.com/oauth/authorize"
        f"?client_id={STRAVA_CLIENT_ID}"
        "&response_type=code"
        f"&redirect_uri={STRAVA_REDIRECT_URI}"
        "&approval_prompt=auto"
        "&scope=read,activity:read_all"
        "&state=glyco"
    )
    return RedirectResponse(url=url)


@app.post("/sync/strava")
def sync_strava(request: Request, replace: bool = Query(False)):
    # Placeholder: succeed if any token exists, else return 'strava_not_connected'
    _ = _decode_cookie(request)
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM strava_tokens")
    cnt = int(cur.fetchone()[0] or 0)
    if cnt == 0:
        con.close()
        raise HTTPException(status_code=400, detail="strava_not_connected")
    if replace:
        cur.execute("DELETE FROM activities")  # demo
        con.commit()
    cur.execute("SELECT COUNT(*) FROM activities")
    total = int(cur.fetchone()[0] or 0)
    con.close()
    return {"ok": True, "inserted": 0, "total": total, "replaced": replace}


# ───────────────────────────────────────────────
# Daily Plan API (used by plan.js)
# ───────────────────────────────────────────────
def _get_lock(user_sub: str, date_iso: str) -> bool:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT locked FROM plan_locks WHERE user_sub=? AND date=?", (user_sub, date_iso))
    row = cur.fetchone()
    con.close()
    return bool(row and row[0])


def _set_lock(user_sub: str, date_iso: str, lock: bool):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO plan_locks (user_sub, date, locked) VALUES (?, ?, ?)",
        (user_sub, date_iso, 1 if lock else 0),
    )
    con.commit()
    con.close()


def _seed_int(s: str) -> int:
    return sum(bytearray(s.encode())) % 97


def _daily_meals(diet: str, date_iso: str, tweak: int = 0) -> list[dict[str, Any]]:
    seed = (_seed_int(date_iso) + tweak) % 100
    breakfast = {
        "title": "Breakfast bowl",
        "meal_type": "breakfast",
        "kcal": 380 + (seed % 50),
        "protein_g": 25,
        "carbs_g": 42,
        "fat_g": 12,
        "ingredients": ["oats", "greek yogurt", "berries", "chia seeds", "honey"],
        "tags": ["breakfast", diet],
        "instructions": "Combine oats with yogurt. Top with berries/chia and drizzle honey.",
    }
    lunch = {
        "title": "Lunch wrap",
        "meal_type": "lunch",
        "kcal": 580 + (seed % 60),
        "protein_g": 35,
        "carbs_g": 55,
        "fat_g": 18,
        "ingredients": [
            "whole wheat tortillas",
            "chicken breast",
            "lettuce",
            "tomatoes",
            "avocado",
            "yogurt sauce",
        ],
        "tags": ["lunch", diet],
        "instructions": "Fill tortillas with chicken, lettuce, tomatoes and avocado.",
    }
    dinner = {
        "title": "Dinner plate",
        "meal_type": "dinner",
        "kcal": 680 + (seed % 70),
        "protein_g": 40,
        "carbs_g": 60,
        "fat_g": 22,
        "ingredients": ["salmon", "rice", "broccoli", "olive oil", "lemon"],
        "tags": ["dinner", diet],
        "instructions": "Bake salmon, steam broccoli, cook rice.",
    }
    snack = {
        "title": "Protein snack" if (seed % 2 == 0) else "Yogurt parfait",
        "meal_type": "snack",
        "kcal": 220 + (seed % 30),
        "protein_g": 18,
        "carbs_g": 18,
        "fat_g": 8,
        "ingredients": (["protein shake", "banana"] if (seed % 2 == 0) else ["greek yogurt", "granola", "berries"]),
        "tags": ["snack", diet],
        "instructions": "Assemble and enjoy.",
    }
    d = (diet or "omnivore").lower()
    if d in ("vegan", "vegetarian"):
        breakfast["ingredients"] = ["oats", "plant yogurt", "berries", "chia seeds", "maple syrup"]
        lunch["ingredients"] = [
            "whole wheat tortillas",
            "tempeh",
            "lettuce",
            "tomatoes",
            "avocado",
            "tahini sauce",
        ]
        dinner["ingredients"] = ["tofu", "rice", "broccoli", "olive oil", "lemon"]
        snack["ingredients"] = ["plant yogurt", "granola", "berries"]
    elif d == "pescatarian":
        lunch["ingredients"] = [
            "whole wheat tortillas",
            "tuna",
            "lettuce",
            "tomatoes",
            "avocado",
            "yogurt sauce",
        ]
    elif d == "keto":
        for m in (breakfast, lunch, dinner, snack):
            m["carbs_g"] = max(5, m["carbs_g"] - 28)
            m["fat_g"] += 15
    return [breakfast, lunch, dinner, snack]


def _totals(meals: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "kcal": sum(int(m.get("kcal", 0)) for m in meals),
        "protein_g": sum(int(m.get("protein_g", 0)) for m in meals),
        "carbs_g": sum(int(m.get("carbs_g", 0)) for m in meals),
        "fat_g": sum(int(m.get("fat_g", 0)) for m in meals),
    }


def _targets(meals: list[dict[str, Any]], training_kcal: int) -> dict[str, int]:
    t = _totals(meals)
    return {
        "tdee_kcal": t["kcal"],
        "training_kcal": training_kcal,
        "protein_g": t["protein_g"],
        "carbs_g": t["carbs_g"],
        "fat_g": t["fat_g"],
    }


def _grocery_list(meals: list[dict[str, Any]]) -> list[str]:
    items: dict[str, int] = {}
    for m in meals:
        for ing in m.get("ingredients", []):
            k = (ing or "").strip().lower()
            if not k:
                continue
            items[k] = items.get(k, 0) + 1
    return sorted(items.keys())


def _sum_training_kcal_for_day(user_sub: str, date_iso: str) -> int:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "SELECT COALESCE(SUM(kcal),0) FROM activities WHERE user_sub=? AND substr(start_time,1,10)=?",
        (user_sub, date_iso),
    )
    total = int(cur.fetchone()[0] or 0)
    con.close()
    return total


@app.get("/v1/plan/{the_date}")
def plan_day(the_date: str, request: Request, diet_pref: str = Query("omnivore")):
    u = _decode_cookie(request)
    meals = _daily_meals(diet_pref, the_date, tweak=0)
    training_kcal = _sum_training_kcal_for_day(u["sub"], the_date)
    return {
        "date": the_date,
        "diet_pref": diet_pref,
        "locked": _get_lock(u["sub"], the_date),
        "meals": meals,
        "grocery_list": _grocery_list(meals),
        "totals": _totals(meals),
        "targets": _targets(meals, training_kcal),
    }


@app.post("/v1/plan/{the_date}/swap")
def plan_swap(the_date: str, request: Request, meal_type: str = Query("snack")):
    u = _decode_cookie(request)
    meals = _daily_meals("omnivore", the_date, tweak=1)
    training_kcal = _sum_training_kcal_for_day(u["sub"], the_date)
    return {
        "date": the_date,
        "diet_pref": "omnivore",
        "locked": _get_lock(u["sub"], the_date),
        "meals": meals,
        "grocery_list": _grocery_list(meals),
        "totals": _totals(meals),
        "targets": _targets(meals, training_kcal),
    }


@app.post("/v1/plan/{the_date}/lock")
def plan_lock(the_date: str, request: Request, lock: str = Query("true")):
    u = _decode_cookie(request)
    _set_lock(u["sub"], the_date, lock.lower() == "true")
    return {"ok": True, "date": the_date, "locked": lock.lower() == "true"}


@app.get("/v1/plan/{the_date}/grocery.txt")
def plan_grocery_txt(the_date: str, request: Request, diet_pref: str = Query("omnivore")):
    _ = _decode_cookie(request)
    items = _grocery_list(_daily_meals(diet_pref, the_date, tweak=0))
    return PlainTextResponse("\n".join(f"- {i}" for i in items), media_type="text/plain; charset=utf-8")


@app.get("/v1/plan/{the_date}/grocery.csv")
def plan_grocery_csv(the_date: str, request: Request, diet_pref: str = Query("omnivore")):
    _ = _decode_cookie(request)
    items = _grocery_list(_daily_meals(diet_pref, the_date, tweak=0))
    content = "item\n" + "\n".join('"' + i.replace('"', '""') + '"' for i in items)
    return PlainTextResponse(content, media_type="text/csv; charset=utf-8")


# ───────────────────────────────────────────────
# Static UI
# ───────────────────────────────────────────────
if SERVE_UI_FROM_API:
    ui_dir = os.path.join(os.path.dirname(__file__), "..", "ui")
    app.mount("/ui", StaticFiles(directory=ui_dir), name="ui")

    @app.get("/", response_class=HTMLResponse)
    def root():
        return RedirectResponse(url="/ui/plan.html")


# ───────────────────────────────────────────────
# Local run
# ───────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.main:app", host=API_HOST, port=API_PORT, reload=True)
