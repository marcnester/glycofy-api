# app/routers/oauth_google.py
from __future__ import annotations

import os
import secrets
import urllib.parse
from datetime import timedelta

import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth_utils import create_access_token
from app.config import settings
from app.db import get_db
from app.models import User

router = APIRouter()

GOOGLE_CLIENT_ID = getattr(settings, "GOOGLE_CLIENT_ID", os.getenv("GOOGLE_CLIENT_ID"))
GOOGLE_CLIENT_SECRET = getattr(settings, "GOOGLE_CLIENT_SECRET", os.getenv("GOOGLE_CLIENT_SECRET"))
GOOGLE_REDIRECT_URI = getattr(settings, "GOOGLE_REDIRECT_URI", os.getenv("GOOGLE_REDIRECT_URI"))

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
OAUTH_SCOPE = "openid email profile"


def _cookie_secure() -> bool:
    # Set COOKIE_SECURE=1 when serving over HTTPS; leave unset on http://localhost
    return str(os.getenv("COOKIE_SECURE", "")).lower() in {"1", "true", "yes"}


def _configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URI)


@router.get("/google/enabled", summary="Is Google OAuth configured?")
def google_enabled():
    return {"enabled": _configured()}


@router.get("/google/start", summary="Start Google OAuth")
def google_start(request: Request):
    if not _configured():
        # Keep 503 for direct hits, but UI will call /enabled first and hide the button.
        raise HTTPException(status_code=503, detail="Google OAuth not configured")

    state = secrets.token_urlsafe(24)
    authorize_url = _google_authorize_url(state)

    resp = RedirectResponse(url=authorize_url)
    resp.set_cookie(
        key="oauth_state",
        value=state,
        max_age=600,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        path="/",
    )
    return resp


def _google_authorize_url(state: str) -> str:
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": OAUTH_SCOPE,
        "state": state,
        "access_type": "offline",
        # For first-time local testing you may keep 'prompt=consent'.
        # Remove or change to 'select_account' later if desired.
        "prompt": "consent",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


@router.get("/google/callback", summary="Google OAuth callback")
def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    db: Session = Depends(get_db),
):
    if not _configured():
        raise HTTPException(status_code=503, detail="Google OAuth not configured")

    cookie_state = request.cookies.get("oauth_state")
    if not state or not cookie_state or state != cookie_state:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    if not code:
        raise HTTPException(status_code=400, detail="Missing code")

    # Exchange code for tokens
    data = {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    token_res = requests.post(TOKEN_URL, data=data, timeout=15)
    if token_res.status_code != 200:
        raise HTTPException(status_code=400, detail="Token exchange failed")

    tokens = token_res.json()
    access_token = tokens.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="No access_token from Google")

    # Fetch userinfo
    ui_res = requests.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
    if ui_res.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to fetch user info")
    info = ui_res.json()

    email = info.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="No email from Google userinfo")

    # Upsert user
    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(
            email=email,
            sex=None,
            height_cm=None,
            weight_kg=None,
            diet_pref="omnivore",
            goal="maintain",
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    # Mint our app token (cookie only; frontend never sees it)
    from app.auth_utils import (
        ACCESS_TOKEN_EXPIRE_MINUTES,
    )  # import here to avoid cycles

    app_token = create_access_token(user.id, minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    resp = RedirectResponse(url="/ui/")
    resp.set_cookie(
        key="access_token",
        value=app_token,
        max_age=int(timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES).total_seconds()),
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        path="/",
    )
    resp.delete_cookie("oauth_state", path="/")
    return resp
