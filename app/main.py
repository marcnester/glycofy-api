# app/main.py
from __future__ import annotations

import logging
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.observability import (
    access_logger,
    configure_logging,
    emit_security_log,
    new_request_id,
    request_id_context,
)

configure_logging()
from app.routers import user_profile, weekly_plans

# --- load .env early ---
try:
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(".env")) or load_dotenv(find_dotenv(".eng"))
except Exception:
    pass

# Routers
from app.routers import (
    activities as activities_router,
)
from app.routers import (
    auth as auth_router,
)
from app.routers import (
    energy as energy_router,
)
from app.routers import (
    health as health_router,
)
from app.routers import (
    imports as imports_router,
)
from app.routers import (
    llm_recommend as llm_recommend_router,
)
from app.routers import (
    meal_feedback as meal_feedback_router,
)
from app.routers import (
    oauth_google as oauth_google_router,
)
from app.routers import (
    oauth_strava as oauth_strava_router,
)
from app.routers import operations as operations_router
from app.routers import (
    plans as plans_router,
)
from app.routers import (
    preferences as preferences_router,  # ← NEW
)
from app.routers import (
    recipes as recipes_router,
)

# --- DEV-only recipes import router (mounted below) ---
from app.routers import recipes_admin as recipes_admin_router  # ← existing dev import
from app.routers import (
    summary as summary_router,
)
from app.routers import training_events as training_events_router
from app.routers import (
    users as users_router,
)

# Optional dashboard
HAS_DASHBOARD = False
DASHBOARD_IMPORT_ERROR = None
try:
    from app.routers import dashboard as dashboard_router

    HAS_DASHBOARD = True
except Exception as _e:
    DASHBOARD_IMPORT_ERROR = str(_e)

APP_DIR = Path(__file__).resolve().parent
UI_DIR = APP_DIR.parent / "ui"

app = FastAPI(
    title="Glycofy API",
    version="0.1",
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)


@app.on_event("startup")
def recover_interrupted_weekly_plans() -> None:
    # Importing lazily keeps application boot order deterministic and submits
    # recovered jobs only after the database migration pre-deploy step.
    if settings.is_production:
        llm_recommend_router.reconcile_weekly_jobs()


# -----------------------------
# CORS
# -----------------------------
ALLOWED_ORIGINS = settings.csv_values(settings.ALLOWED_ORIGINS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Requested-With"],
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.csv_values(settings.ALLOWED_HOSTS))


@app.middleware("http")
async def security_controls(request: Request, call_next):
    correlation_id = new_request_id(request.headers.get("x-request-id"))
    context_token = request_id_context.set(correlation_id)
    started = time.perf_counter()

    def reject(message: str, status_code: int, event_type: str) -> PlainTextResponse:
        emit_security_log(event_type, "denied", severity="warning")
        response = PlainTextResponse(message, status_code=status_code)
        response.headers["X-Request-ID"] = correlation_id
        access_logger.warning(
            "request_rejected",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        request_id_context.reset(context_token)
        return response

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > settings.MAX_REQUEST_BODY_BYTES:
                return reject("Request body too large", 413, "request_body_rejected")
        except ValueError:
            return reject("Invalid Content-Length", 400, "invalid_content_length")

    # Cookie-authenticated unsafe requests must be same-origin. Bearer-only API
    # clients normally omit Origin and are not vulnerable to browser CSRF.
    if request.method not in {"GET", "HEAD", "OPTIONS", "TRACE"}:
        origin = request.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            origin_value = f"{parsed.scheme}://{parsed.netloc}"
            if origin_value not in ALLOWED_ORIGINS:
                return reject("Cross-origin request denied", 403, "csrf_origin_rejected")

    try:
        response = await call_next(request)
    except Exception:
        emit_security_log("unhandled_request_exception", "error", severity="alert")
        logging.getLogger("glycofy.application").exception("unhandled_request_exception")
        request_id_context.reset(context_token)
        raise
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
        "form-action 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'",
    )
    if request.url.path.startswith(("/auth", "/oauth")):
        response.headers.setdefault("Cache-Control", "no-store")
    if settings.is_production:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    response.headers["X-Request-ID"] = correlation_id
    access_logger.info(
        "request_complete",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        },
    )
    request_id_context.reset(context_token)
    return response


# -----------------------------
# API Routers
# -----------------------------
app.include_router(health_router.router, prefix="", tags=["health"])
app.include_router(auth_router.router, prefix="/auth", tags=["auth"])
app.include_router(users_router.router, tags=["users"])
app.include_router(activities_router.router, prefix="/activities", tags=["activities"])
app.include_router(plans_router.router, prefix="/v1/plan", tags=["plan"])
app.include_router(recipes_router.router, prefix="/recipes", tags=["recipes"])
app.include_router(summary_router.router, prefix="/v1", tags=["summary"])
app.include_router(imports_router.router, prefix="/imports", tags=["imports"])
app.include_router(meal_feedback_router.router, prefix="/v1/feedback", tags=["meal-feedback"])
app.include_router(energy_router.router, prefix="/v1/energy", tags=["energy"])
app.include_router(llm_recommend_router.router, prefix="/v1/llm", tags=["llm"])
app.include_router(operations_router.router, prefix="/v1/operations", tags=["operations"])
app.include_router(preferences_router.router, prefix="/v1/preferences", tags=["preferences"])  # ← NEW
app.include_router(training_events_router.router)
app.include_router(user_profile.router)
app.include_router(weekly_plans.router)

# OAuth routers
app.include_router(oauth_google_router.router, prefix="/oauth/google", tags=["oauth/google"])
app.include_router(oauth_strava_router.router, prefix="/oauth/strava", tags=["oauth/strava"])
app.include_router(oauth_strava_router.sync_router, prefix="/sync/strava", tags=["sync/strava"])

# Dashboard (optional)
if HAS_DASHBOARD:
    app.include_router(dashboard_router.router, prefix="/dashboard", tags=["dashboard"])
else:

    @app.get("/dashboard/today", include_in_schema=False)
    def _dashboard_not_available():
        return {"error": "dashboard router not loaded", "detail": DASHBOARD_IMPORT_ERROR}


# --- DEV-only: recipes import endpoint ---
if settings.ENABLE_DEV_ROUTES and not settings.is_production:
    app.include_router(recipes_admin_router.router, prefix="/dev/recipes", tags=["dev"])

# -----------------------------
# Static UI
# -----------------------------
if not UI_DIR.exists():
    UI_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/ui", StaticFiles(directory=str(UI_DIR), html=True), name="ui")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/ui/")


@app.get("/ui", include_in_schema=False)
def ui_no_slash():
    return RedirectResponse(url="/ui/")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    svg = UI_DIR / "favicon.svg"
    if svg.exists():
        return FileResponse(svg, media_type="image/svg+xml")
    return PlainTextResponse("", status_code=204)
