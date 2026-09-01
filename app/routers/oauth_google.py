# app/routers/oauth_google.py
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import OAuthAccount, User
from app.observability import record_security_event
from app.rate_limit import AUTH_LIMITER, client_key
from app.routers.auth import COOKIE_ACCESS, _cookie_kwargs, _create_access_token, hash_password

router = APIRouter()

# ---- Config from env ----
GOOGLE_CLIENT_ID = (settings.GOOGLE_CLIENT_ID or "").strip()
GOOGLE_CLIENT_SECRET = (settings.GOOGLE_CLIENT_SECRET or "").strip()
GOOGLE_REDIRECT_URL = (settings.GOOGLE_REDIRECT_URI or settings.GOOGLE_REDIRECT_URL or "").strip()

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


def _oauth_cookie_kwargs() -> dict:
    return {
        "httponly": True,
        "secure": settings.COOKIE_SECURE,
        "samesite": "lax",
        "path": "/oauth/google/callback",
        "max_age": settings.OAUTH_STATE_TTL_SECONDS,
    }


def _now_ts() -> int:
    return int(time.time())


def _encode_state() -> str:
    payload = {
        "exp": _now_ts() + settings.OAUTH_STATE_TTL_SECONDS,
        "nonce": secrets.token_urlsafe(24),
    }
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(settings.JWT_SECRET.encode(), body.encode(), hashlib.sha256).digest()
    sig = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{body}.{sig}"


def _decode_state(state: str) -> None:
    try:
        body, supplied_sig = state.split(".", 1)
    except ValueError as exc:
        raise ValueError("bad_state") from exc
    expected = hmac.new(settings.JWT_SECRET.encode(), body.encode(), hashlib.sha256).digest()
    expected_sig = base64.urlsafe_b64encode(expected).decode().rstrip("=")
    if not hmac.compare_digest(supplied_sig, expected_sig):
        raise ValueError("bad_state")
    try:
        pad = "=" * ((4 - len(body) % 4) % 4)
        payload = json.loads(base64.urlsafe_b64decode(body + pad))
        expires = int(payload["exp"])
        nonce = payload["nonce"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("bad_state") from exc
    if not isinstance(nonce, str) or len(nonce) < 24:
        raise ValueError("bad_state")
    if expires <= _now_ts():
        raise ValueError("expired_state")


def verify_state(request: Request, state_from_query: str) -> None:
    state_cookie = request.cookies.get(STATE_COOKIE_NAME, "")
    if not state_cookie or not state_from_query or not hmac.compare_digest(state_cookie, state_from_query):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    try:
        _decode_state(state_from_query)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid OAuth state") from exc


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
async def google_start(
    request: Request,
    return_: str | None = Query(default=None, alias="return"),
) -> Response:
    """
    Begin OAuth:
      - create cryptographically-strong state
      - store state + return (if provided) in httpOnly cookies
      - redirect to Google authorization URL
    """
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URL):
        raise HTTPException(status_code=500, detail="Google OAuth is not configured")

    AUTH_LIMITER.check(
        f"oauth-google-start:ip:{client_key(request)}",
        maximum=settings.OAUTH_RATE_LIMIT_PER_15_MINUTES,
        window_seconds=900,
    )

    # Signed, expiring state for CSRF protection.
    nonce = _encode_state()

    # Store state + return in cookies
    resp = RedirectResponse(url="/")  # will be replaced
    resp.set_cookie(
        STATE_COOKIE_NAME,
        nonce,
        max_age=settings.OAUTH_STATE_TTL_SECONDS,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/oauth/google/callback",
    )
    safe_return = _safe_return_path(return_ or DEFAULT_RETURN_PATH)
    resp.set_cookie(
        RETURN_COOKIE_NAME,
        safe_return,
        max_age=settings.OAUTH_STATE_TTL_SECONDS,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/",
    )

    # Build Google auth URL
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URL,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": nonce,
        "prompt": "select_account",
    }
    url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
    resp.headers["Location"] = url
    resp.status_code = 302
    return resp


@router.get("/callback")
async def google_callback(
    request: Request,
    background_tasks: BackgroundTasks,
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

    try:
        AUTH_LIMITER.check(
            f"oauth-google-callback:ip:{client_key(request)}",
            maximum=settings.OAUTH_RATE_LIMIT_PER_15_MINUTES,
            window_seconds=900,
        )
    except HTTPException:
        record_security_event(db, request, "oauth_google_rate_limited", "denied", severity="alert")
        raise

    try:
        verify_state(request, state)
    except HTTPException:
        record_security_event(db, request, "oauth_google_callback", "invalid_state", severity="alert")
        raise

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
            record_security_event(
                db,
                request,
                "oauth_google_callback",
                "token_exchange_failure",
                severity="warning",
                details={"provider_status": token_res.status_code},
            )
            raise HTTPException(
                status_code=400,
                detail="Google token exchange failed",
            )

        token = token_res.json()
        access_token = token.get("access_token")
        scope = token.get("scope")

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
                detail="Google user profile request failed",
            )

        profile = ures.json()
        email = (profile.get("email") or "").lower()
        sub = profile.get("sub")
        locale = (profile.get("locale") or "").strip()

        if not email:
            raise HTTPException(status_code=400, detail="Google profile missing email")
        if profile.get("email_verified") is not True:
            record_security_event(
                db,
                request,
                "oauth_google_callback",
                "unverified_email",
                severity="warning",
            )
            raise HTTPException(status_code=400, detail="Google email is not verified")
        if not sub:
            raise HTTPException(status_code=400, detail="Google profile missing subject")

    # Find or create user
    user = db.query(User).filter(User.email == email).first()
    if not user:
        # OAuth-only users still satisfy the legacy non-null password column,
        # but the generated value is unknown and cannot be used to log in.
        user = User(email=email, password_hash=hash_password(secrets.token_urlsafe(48)))
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
    if account:
        account.external_athlete_id = sub or account.external_athlete_id
        # Google is used only for authentication. Do not retain bearer or
        # refresh tokens that Glycofy does not need after fetching userinfo.
        account.access_token = None
        account.refresh_token = None
        account.expires_at = None
        if scope:
            account.scope = scope
    else:
        account = OAuthAccount(
            user_id=user.id,
            provider="google",
            external_athlete_id=sub,
            access_token=None,
            refresh_token=None,
            expires_at=None,
            scope=scope,
        )
        db.add(account)
    db.commit()
    record_security_event(db, request, "oauth_google_login", "success", user_id=user.id)

    # Mint JWT and set cookies
    app_jwt = _create_access_token(
        str(user.id), minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES, token_version=user.token_version
    )

    # Safe redirect path
    dest = _safe_return_path(request.cookies.get(RETURN_COOKIE_NAME))

    resp = RedirectResponse(url=dest, status_code=302)
    # Set cookies
    resp.set_cookie(COOKIE_ACCESS, app_jwt, **_cookie_kwargs(http_only=True))
    for legacy_name in ("glyco_auth", "id_token", "glyco_token"):
        resp.delete_cookie(legacy_name, path="/", samesite="lax")

    # Clean up
    resp.delete_cookie(STATE_COOKIE_NAME, path="/oauth/google/callback")
    resp.delete_cookie(RETURN_COOKIE_NAME, path="/")
    from app.routers.oauth_strava import schedule_strava_sync_on_login

    schedule_strava_sync_on_login(background_tasks=background_tasks, db=db, user_id=user.id)
    return resp
