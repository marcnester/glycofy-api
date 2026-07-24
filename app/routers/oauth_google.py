# app/routers/oauth_google.py
from __future__ import annotations

import base64
import os
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import OAuthAccount, User
from app.routers.auth import _create_access_token  # reuse same JWT helper

router = APIRouter()

# ---- Config from env ----
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_REDIRECT_URL = os.environ.get("GOOGLE_REDIRECT_URL", "").strip()

# Cookie names (align with auth.py)
COOKIE_ACCESS = "access_token"
COOKIE_LEGACY = "glyco_auth"
COOKIE_ID = "id_token"
COOKIE_READABLE = "glyco_token"

# Temp cookies for OAuth round-trip
STATE_COOKIE_NAME = "oauth_state"
RETURN_COOKIE_NAME = "oauth_return"

# Google endpoints
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

SCOPES = ["openid", "email", "profile"]

# ---- constants --------------------------------------------------------------
DEFAULT_RETURN_PATH = "/ui/index.html"  # default destination after login


# ---- helpers ----------------------------------------------------------------


def _cookie_kwargs_dev() -> dict:
    return {
        "httponly": True,
        "secure": False,
        "samesite": "lax",
        "path": "/",
        "max_age": 60 * 60 * 24 * 14,
    }


def verify_state(request: Request, state_from_query: str) -> None:
    state_cookie = request.cookies.get(STATE_COOKIE_NAME, "")
    if not state_cookie or not state_from_query or state_cookie != state_from_query:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")


def _units_from_locale(locale: str | None) -> str | None:
    if not locale:
        return None
    l = locale.replace("_", "-").lower()
    return "US" if l.startswith("en-us") else "Metric"


def _safe_return_path(val: str | None) -> str:
    """Only allow same-origin /ui paths; default to /ui/index.html."""
    if isinstance(val, str) and val.startswith("/") and not val.startswith("//"):
        return val
    return DEFAULT_RETURN_PATH


def _derive_full_name(profile: dict, email: str, existing_display: str | None) -> str | None:
    """
    Build the best full name we can from Google's profile + email.

    Preference:
      1) profile["name"]
      2) "<given_name> <family_name>"
      3) email local-part (as a last resort)

    We return None if we truly have nothing better.
    """
    # 1) Full name as provided by Google
    full_name = (profile.get("name") or "").strip()

    # 2) Fallback to given + family name
    if not full_name:
        given = (profile.get("given_name") or "").strip()
        family = (profile.get("family_name") or "").strip()
        if given or family:
            full_name = f"{given} {family}".strip()

    # 3) Final fallback: email local-part
    if not full_name and email:
        full_name = email.split("@", 1)[0]

    full_name = full_name.strip()
    if not full_name:
        return None

    # If the existing display_name is already a nicer name, you could choose
    # to keep it. For now, we just return the derived name; the caller will
    # decide whether to overwrite.
    return full_name


# ---- routes ------------------------------------------------------------------


@router.get("/status")
async def google_status() -> dict:
    return {"configured": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URL)}


@router.get("/start")
async def google_start(request: Request, return_: str | None = None) -> Response:
    """
    Begin OAuth:
      - create cryptographically-strong state
      - store state + return (if provided) in httpOnly cookies
      - redirect to Google authorization URL
    """
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URL):
        raise HTTPException(status_code=500, detail="Google OAuth is not configured")

    # random state for CSRF protection
    nonce = base64.urlsafe_b64encode(os.urandom(24)).decode("ascii").rstrip("=")

    # Store state + return in cookies
    resp = RedirectResponse(url="/")  # will be replaced
    resp.set_cookie(
        STATE_COOKIE_NAME,
        nonce,
        max_age=600,
        httponly=True,
        secure=False,
        samesite="lax",
        path="/",
    )
    safe_return = _safe_return_path(return_ or DEFAULT_RETURN_PATH)
    resp.set_cookie(
        RETURN_COOKIE_NAME,
        safe_return,
        max_age=600,
        httponly=True,
        secure=False,
        samesite="lax",
        path="/",
    )

    # Build Google auth URL
    from urllib.parse import urlencode

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URL,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": nonce,
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "select_account",
    }
    url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
    resp.headers["Location"] = url
    resp.status_code = 302
    return resp


@router.get("/callback")
async def google_callback(
    request: Request,
    db: Session = Depends(get_db),
    code: str | None = None,
    state: str | None = None,
) -> Response:
    """
    Complete OAuth:
      - verify state
      - exchange code for tokens
      - fetch userinfo
      - find-or-create/enrich local user
      - mint JWT and set cookies
      - redirect to original return or /ui/index.html
    """
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")
    if not state:
        raise HTTPException(status_code=400, detail="Missing state")

    verify_state(request, state)

    # Exchange code for tokens
    async with httpx.AsyncClient(timeout=15.0) as client:
        token_payload = {
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": GOOGLE_REDIRECT_URL,
            "grant_type": "authorization_code",
        }
        token_res = await client.post(
            GOOGLE_TOKEN_URL,
            data=token_payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if token_res.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail=f"Token exchange failed: {token_res.text}",
            )

        token = token_res.json()
        access_token = token.get("access_token")
        refresh_token = token.get("refresh_token")
        expires_in = token.get("expires_in")
        scope = token.get("scope")
        id_token = token.get("id_token") or ""

        if not access_token:
            raise HTTPException(status_code=400, detail="No access_token in token response")

        # Fetch user info
        ures = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if ures.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail=f"Userinfo fetch failed: {ures.text}",
            )

        profile = ures.json()
        email = (profile.get("email") or "").lower()
        sub = profile.get("sub")
        locale = (profile.get("locale") or "").strip()

        if not email:
            raise HTTPException(status_code=400, detail="Google profile missing email")

    # Find or create user
    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(email=email)
        db.add(user)
        db.commit()
        db.refresh(user)

    # Enrich user
    dirty = False

    # --- Name / display_name mapping ---
    try:
        existing_display = getattr(user, "display_name", None)
        best_name = _derive_full_name(profile, email, existing_display)

        # Only overwrite if:
        #  - there was no display_name before, OR
        #  - it was just the raw email local-part (e.g. "marcnester")
        email_local = email.split("@", 1)[0] if email else None
        should_overwrite = best_name and (not existing_display or (email_local and existing_display == email_local))
        if should_overwrite:
            user.display_name = best_name
            dirty = True

        # Optional: if your User model has a "name" field, keep it in sync.
        if hasattr(user, "name"):
            existing_name = getattr(user, "name", None)
            if best_name and (not existing_name or existing_name == email_local):
                user.name = best_name
                dirty = True
    except Exception:
        # Don't break login flow because of name mapping
        pass

    # --- Units from locale (unchanged) ---
    try:
        if not getattr(user, "units", None):
            units = _units_from_locale(locale)
            if units:
                user.units = units
                dirty = True
    except Exception:
        pass

    if dirty:
        db.add(user)
        db.commit()
        db.refresh(user)

    # Upsert oauth_accounts
    account = db.query(OAuthAccount).filter(OAuthAccount.user_id == user.id, OAuthAccount.provider == "google").first()
    now_exp = None
    if isinstance(expires_in, int):
        now_exp = int(time.time()) + int(expires_in)
    if account:
        account.external_athlete_id = sub or account.external_athlete_id
        account.access_token = access_token or account.access_token
        if refresh_token:
            account.refresh_token = refresh_token
        if now_exp:
            account.expires_at = now_exp
        if scope:
            account.scope = scope
    else:
        account = OAuthAccount(
            user_id=user.id,
            provider="google",
            external_athlete_id=sub,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=now_exp,
            scope=scope,
        )
        db.add(account)
    db.commit()

    # Mint JWT and set cookies
    app_jwt = _create_access_token(str(user.id), minutes=60)

    # Safe redirect path
    dest = _safe_return_path(request.cookies.get(RETURN_COOKIE_NAME))

    resp = RedirectResponse(url=dest, status_code=302)
    # Set cookies
    resp.set_cookie(COOKIE_ACCESS, app_jwt, **_cookie_kwargs_dev())
    resp.set_cookie(COOKIE_LEGACY, app_jwt, **_cookie_kwargs_dev())
    if id_token:
        resp.set_cookie(COOKIE_ID, id_token, **_cookie_kwargs_dev())
    resp.set_cookie(
        COOKIE_READABLE,
        app_jwt,
        httponly=False,
        secure=False,
        samesite="lax",
        path="/",
        max_age=60 * 60 * 24 * 14,
    )

    # Clean up
    resp.delete_cookie(STATE_COOKIE_NAME, path="/")
    resp.delete_cookie(RETURN_COOKIE_NAME, path="/")
    return resp
