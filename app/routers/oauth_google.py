# app/routers/oauth_google.py
from __future__ import annotations

import os
import secrets
import time
from typing import Optional, Tuple

import requests
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User, OAuthAccount
from app.auth_utils import create_access_token

router = APIRouter()

# ---- Config helpers ---------------------------------------------------------

def _google_cfg() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    client_id = getattr(settings, "GOOGLE_CLIENT_ID", None) or os.getenv("GOOGLE_CLIENT_ID")
    client_secret = getattr(settings, "GOOGLE_CLIENT_SECRET", None) or os.getenv("GOOGLE_CLIENT_SECRET")
    redirect_uri = getattr(settings, "GOOGLE_REDIRECT_URL", None) or os.getenv("GOOGLE_REDIRECT_URL")
    return client_id, client_secret, redirect_uri


def _is_configured() -> bool:
    cid, sec, redir = _google_cfg()
    return bool(cid and sec and redir)


# ---- Introspection endpoint for the UI -------------------------------------

@router.get("/google/status")
def google_status():
    """Used by /ui/login.js to decide whether to show the Google button."""
    return {"configured": _is_configured()}


# ---- Start OAuth ------------------------------------------------------------

@router.get("/google/start")
def google_start(request: Request, response: Response):
    """
    Redirect the browser to Google's consent screen.
    Stores a short-lived `oauth_google_state` cookie for CSRF protection.
    """
    client_id, client_secret, redirect_uri = _google_cfg()
    if not _is_configured():
        raise HTTPException(status_code=503, detail="Google OAuth not configured")

    # CSRF state
    state = secrets.token_urlsafe(24)
    # 10 minutes should be plenty
    response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key="oauth_google_state",
        value=state,
        httponly=True,
        samesite="lax",
        max_age=600,
        path="/",
    )

    scope = "openid email profile https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/userinfo.profile"
    auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        "&response_type=code"
        f"&scope={requests.utils.quote(scope)}"
        f"&state={state}"
        "&access_type=online"
        "&include_granted_scopes=true"
        # Optional: "&prompt=consent"
    )
    response.headers["Location"] = auth_url
    return response


# ---- Callback ---------------------------------------------------------------

@router.get("/google/callback")
def google_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    Google redirects here with ?code=...&state=...
    We verify state, exchange code for tokens, fetch profile, create/upsert user,
    set our HttpOnly JWT cookie, and redirect to the UI.
    """
    client_id, client_secret, redirect_uri = _google_cfg()
    if not _is_configured():
        raise HTTPException(status_code=503, detail="Google OAuth not configured")

    # CSRF state check
    expected_state = request.cookies.get("oauth_google_state")
    if not expected_state or not state or state != expected_state:
        raise HTTPException(status_code=400, detail="Invalid or missing OAuth state")

    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    # 1) Exchange authorization code for tokens
    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    try:
        tr = requests.post(token_url, data=data, timeout=15)
    except requests.RequestException as ex:
        raise HTTPException(status_code=502, detail=f"Token exchange failed: {ex}")

    if tr.status_code != 200:
        detail = tr.text
        raise HTTPException(status_code=400, detail=f"Token exchange error: {detail}")

    tok = tr.json()
    access_token = tok.get("access_token")
    refresh_token = tok.get("refresh_token")
    expires_in = tok.get("expires_in")  # seconds
    id_token = tok.get("id_token")  # not strictly needed here

    if not access_token:
        raise HTTPException(status_code=400, detail="Missing access_token in Google response")

    # 2) Fetch userinfo
    try:
        ur = requests.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            timeout=15,
        )
    except requests.RequestException as ex:
        raise HTTPException(status_code=502, detail=f"Userinfo request failed: {ex}")

    if ur.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Userinfo error: {ur.text}")

    info = ur.json()
    # Typical fields
    # {
    #   "sub": "...", "email": "name@example.com", "email_verified": true,
    #   "name": "...", "given_name": "...", "family_name": "...", "picture": "...", "locale": "en"
    # }
    google_sub = info.get("sub")
    email = (info.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Google did not provide an email")

    # 3) Upsert user
    user = db.query(User).filter(User.email == email).first()
    if not user:
        # IMPORTANT: your schema has NOT NULL on password_hash.
        # For OAuth-only users, store an empty string to satisfy NOT NULL.
        user = User(
            email=email,
            password_hash="",            # <--- fixes NOT NULL constraint without changing schema
            diet_pref="omnivore",
            goal="maintain",
        )
        db.add(user)
        # flush to get user.id
        db.flush()

    # 4) Upsert OAuthAccount for provider='google'
    # We reuse the existing columns; store Google "sub" in external_athlete_id.
    acct = (
        db.query(OAuthAccount)
        .filter(OAuthAccount.user_id == user.id, OAuthAccount.provider == "google")
        .first()
    )
    expires_at = int(time.time()) + int(expires_in or 3600)
    scope = "email profile openid"

    if acct is None:
        acct = OAuthAccount(
            user_id=user.id,
            provider="google",
            external_athlete_id=google_sub,  # reuse the column
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            scope=scope,
        )
        db.add(acct)
    else:
        acct.external_athlete_id = google_sub
        acct.access_token = access_token
        acct.refresh_token = refresh_token or acct.refresh_token
        acct.expires_at = expires_at
        acct.scope = scope

    db.commit()

    # 5) Issue our app JWT and set HttpOnly cookie
    jwt_token = create_access_token(user.id, minutes=None)  # uses default lifetime
    resp = RedirectResponse(url="/ui/activities.html", status_code=status.HTTP_302_FOUND)
    resp.set_cookie(
        key="access_token",
        value=jwt_token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60,  # match your default 60 minutes
        path="/",
        secure=False,     # set True in production with HTTPS
    )
    # Clear the state cookie
    resp.delete_cookie(key="oauth_google_state", path="/")
    return resp