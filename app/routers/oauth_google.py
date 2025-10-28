from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import quote

import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import OAuthAccount, User  # adjust imports if needed

router = APIRouter(prefix="/oauth", tags=["oauth-google"])

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8090/oauth/google/callback")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
SCOPE = "openid email profile"


def _ensure_configured() -> None:
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="Google OAuth not configured")


@router.get("/google/start-url", summary="Return Google authorization URL")
def start_google_oauth_url() -> dict[str, str]:
    _ensure_configured()
    q = (
        f"client_id={quote(GOOGLE_CLIENT_ID)}"
        f"&redirect_uri={quote(GOOGLE_REDIRECT_URI)}"
        f"&response_type=code"
        f"&scope={quote(SCOPE)}"
        f"&access_type=offline"
        f"&prompt=consent"
    )
    return {"url": f"{AUTH_URL}?{q}"}


@router.get("/google/status", summary="Check if Google OAuth is linked for current user")
def google_status(
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    # If you also want to require auth to check status, accept current_user via Depends
    return {"configured": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)}


@router.get("/google/callback")
def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    db: Session = Depends(get_db),
):
    _ensure_configured()

    if not code:
        raise HTTPException(status_code=400, detail="Missing code")

    data = {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code",
    }

    try:
        tr = requests.post(TOKEN_URL, data=data, timeout=15)
    except requests.RequestException as ex:
        raise HTTPException(status_code=502, detail=f"Token exchange failed: {ex}") from ex

    if tr.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Token exchange error: {tr.text}")

    tok = tr.json()
    access_token: str | None = tok.get("access_token")
    refresh_token: str | None = tok.get("refresh_token")
    expires_in: int | None = tok.get("expires_in")

    if not access_token:
        raise HTTPException(status_code=400, detail="No access_token")

    try:
        ur = requests.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
    except requests.RequestException as ex:
        raise HTTPException(status_code=502, detail=f"Userinfo request failed: {ex}") from ex

    if ur.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Userinfo error: {ur.text}")

    info = ur.json()
    # Normalize and upsert user
    email = (info.get("email") or "").lower()
    name = info.get("name") or email.split("@")[0]

    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(  # type: ignore[call-arg]
            email=email,
            password_hash="",  # OAuth users have no local password
            diet_pref=None,
            goal=None,
        )
        db.add(user)
        db.flush()

    # Link OAuth account record (provider 'google')
    now = int(time.time())
    expires_at = now + int(expires_in or 3600)

    oa = db.query(OAuthAccount).filter(OAuthAccount.user_id == user.id, OAuthAccount.provider == "google").first()
    if not oa:
        oa = OAuthAccount(  # type: ignore[call-arg]
            user_id=user.id,
            provider="google",
            external_athlete_id=None,
            access_token=access_token,
            refresh_token=refresh_token,
            scope=SCOPE,
            expires_at=expires_at,
            linked=True,
        )
        db.add(oa)
    else:
        oa.access_token = access_token
        oa.refresh_token = refresh_token or oa.refresh_token
        oa.expires_at = expires_at
        oa.scope = SCOPE
        oa.linked = True

    db.commit()

    return {"ok": True, "email": email, "name": name}
