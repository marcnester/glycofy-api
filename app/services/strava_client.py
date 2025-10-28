# app/services/strava_client.py
from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import requests

from app.config import settings

STRAVA_OAUTH_BASE = "https://www.strava.com/oauth"
STRAVA_API_BASE = "https://www.strava.com/api/v3"


def strava_configured() -> bool:
    """Return True if all required Strava settings are present."""
    return bool(settings.STRAVA_CLIENT_ID and settings.STRAVA_CLIENT_SECRET and settings.STRAVA_REDIRECT_URI)


def get_authorize_url(state: str | None = None, scope: str | None = None) -> str:
    """
    Build the Strava OAuth authorize URL.
    We support 'state' (so we can round-trip the user id) and a customizable scope.
    """
    if not strava_configured():
        raise RuntimeError("Strava is not configured in settings/.env")

    # Default scope for this app (can be overridden)
    if not scope:
        scope = "read,activity:read_all"

    params = {
        "client_id": settings.STRAVA_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": settings.STRAVA_REDIRECT_URI,
        "scope": scope,
        "approval_prompt": "auto",
    }
    if state is not None:
        params["state"] = state

    return f"{STRAVA_OAUTH_BASE}/authorize?{urlencode(params)}"


# Backward-compatible alias for any existing imports
def build_authorize_url(state: str | None = None, scope: str | None = None) -> str:
    return get_authorize_url(state=state, scope=scope)


def exchange_code_for_tokens(code: str) -> dict[str, Any]:
    """
    Exchange an authorization code for an access+refresh token set.
    https://developers.strava.com/docs/authentication/
    """
    if not strava_configured():
        raise RuntimeError("Strava is not configured in settings/.env")

    url = f"{STRAVA_OAUTH_BASE}/token"
    payload = {
        "client_id": settings.STRAVA_CLIENT_ID,
        "client_secret": settings.STRAVA_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
    }
    resp = requests.post(url, data=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    """
    Refresh an access token using a refresh token.
    """
    if not strava_configured():
        raise RuntimeError("Strava is not configured in settings/.env")

    url = f"{STRAVA_OAUTH_BASE}/token"
    payload = {
        "client_id": settings.STRAVA_CLIENT_ID,
        "client_secret": settings.STRAVA_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    resp = requests.post(url, data=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_with_bearer(path: str, access_token: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Convenience helper to call Strava API (GET) with bearer token.
    """
    url = f"{STRAVA_API_BASE.rstrip('/')}/{path.lstrip('/')}"
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(url, headers=headers, params=params or {}, timeout=30)
    resp.raise_for_status()
    return resp.json()
