# app/routers/oauth_strava.py
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth_utils import get_current_user
from app.db import get_db
from app.models import OAuthAccount, User
from app.services.strava_client import (
    exchange_code_for_tokens,
    get_authorize_url,
    strava_configured,
)

router = APIRouter()  # NOTE: no prefix here; main.py mounts it at /oauth


def _ensure_configured():
    if not strava_configured():
        raise HTTPException(status_code=501, detail="Strava not configured")


@router.get("/status", summary="OAuth provider link status for current user")
def oauth_status(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _ensure_configured()
    acct = (
        db.query(OAuthAccount)
        .filter(
            OAuthAccount.provider == "strava",
            OAuthAccount.user_id == user.id,
        )
        .first()
    )
    if not acct:
        return {
            "provider": "strava",
            "linked": False,
            "scope": None,
            "expires_at": None,
        }

    return {
        "provider": "strava",
        "linked": True,
        "external_athlete_id": acct.external_athlete_id,
        "scope": acct.scope,
        "expires_at": acct.expires_at,
    }


@router.get("/linked", summary="List linked OAuth providers for current user")
def linked_providers(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _ensure_configured()
    rows = db.query(OAuthAccount).filter(OAuthAccount.user_id == user.id).all()
    out = []
    for r in rows:
        out.append(
            {
                "provider": r.provider,
                "external_athlete_id": r.external_athlete_id,
                "linked": True,
                "scope": r.scope,
                "expires_at": r.expires_at,
            }
        )
    return out


@router.get("/start-url", summary="Return Strava authorization URL")
def start_strava_oauth_url(
    user: User = Depends(get_current_user),
):
    _ensure_configured()
    # state should be something we can verify later; user id is fine for MVP
    authorize_url = get_authorize_url(state=str(user.id))
    return {"authorize_url": authorize_url}


@router.get("/start", summary="Redirect user to Strava authorization")
def start_strava_oauth(
    request: Request,
    user: User = Depends(get_current_user),
):
    _ensure_configured()
    url = get_authorize_url(state=str(user.id))
    # Return a simple HTML page with a client-side redirect (no templates needed)
    return f"""<html><head>
           <meta http-equiv="refresh" content="0; url={url}">
           </head><body>Redirecting to Strava… <a href="{url}">{url}</a></body></html>"""


@router.get("/callback", summary="Strava OAuth callback")
def strava_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    _ensure_configured()

    if error:
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")
    if not code:
        raise HTTPException(status_code=400, detail="Missing code")

    # state was set to user.id in start-url/start; validate it’s an int
    try:
        user_id = int(state or "0")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid state")

    user = db.query(User).get(user_id)
    if not user:
        raise HTTPException(status_code=400, detail="Unknown user in state")

    tokens = exchange_code_for_tokens(code)

    # Upsert OAuthAccount
    acct = (
        db.query(OAuthAccount)
        .filter(
            OAuthAccount.provider == "strava",
            OAuthAccount.user_id == user.id,
        )
        .first()
    )
    if not acct:
        acct = OAuthAccount(
            user_id=user.id,
            provider="strava",
            external_athlete_id=str(tokens.get("athlete", {}).get("id", "")),
            access_token=tokens.get("access_token"),
            refresh_token=tokens.get("refresh_token"),
            scope=(",".join(tokens.get("scope", "").split(",")) if tokens.get("scope") else None),
            expires_at=int(tokens.get("expires_at", 0)) or None,
        )
        db.add(acct)
    else:
        acct.external_athlete_id = str(tokens.get("athlete", {}).get("id", "")) or acct.external_athlete_id
        acct.access_token = tokens.get("access_token", acct.access_token)
        acct.refresh_token = tokens.get("refresh_token", acct.refresh_token)
        acct.scope = ",".join(tokens.get("scope", "").split(",")) if tokens.get("scope") else acct.scope
        acct.expires_at = int(tokens.get("expires_at", acct.expires_at or 0)) or acct.expires_at

    db.commit()

    # Simple success page that sends the user back to Profile
    return (
        '<html><body style="font-family:system-ui;padding:24px;'
        'background:#0b0f14;color:#e6edf3">'
        "<h2>Strava connected ✅</h2>"
        "<p>You can close this tab.</p>"
        '<script>setTimeout(()=>window.location="/ui/profile.html",800);</script>'
        "</body></html>"
    )
