# app/routers/oauth_core.py
from __future__ import annotations

import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import settings

router = APIRouter()


def _get_env(name: str) -> str | None:
    val = getattr(settings, name, None)
    return str(val) if val is not None else os.getenv(name)


def _is_strava_configured() -> bool:
    return bool(_get_env("STRAVA_CLIENT_ID") and _get_env("STRAVA_CLIENT_SECRET") and _get_env("STRAVA_REDIRECT_URI"))


def _is_google_configured() -> bool:
    return bool(_get_env("GOOGLE_CLIENT_ID") and _get_env("GOOGLE_CLIENT_SECRET") and _get_env("GOOGLE_REDIRECT_URL"))


@router.get("/status")
async def oauth_status() -> JSONResponse:
    """Aggregate OAuth status for the Profile page."""
    return JSONResponse(
        {
            "strava": {
                "configured": _is_strava_configured(),
                "linked": False,
                "external_athlete_id": None,
                "scope": "activity:read_all",
                "expires_at": None,
            },
            "google": {
                "configured": _is_google_configured(),
                "linked": False,
                "email": None,
            },
        }
    )


@router.get("/linked")
async def linked_providers() -> JSONResponse:
    """Return list of currently linked providers (placeholder)."""
    return JSONResponse({"linked": []})
