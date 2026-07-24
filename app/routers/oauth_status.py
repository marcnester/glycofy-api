# app/routers/oauth_status.py
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.deps import get_current_user, get_db

# Try to import various possible token models without crashing if not present.
try:
    from app.models import StravaToken as _StravaToken  # type: ignore
except Exception:  # pragma: no cover
    _StravaToken = None  # type: ignore

try:
    from app.models import OAuthToken as _OAuthToken  # type: ignore
except Exception:  # pragma: no cover
    _OAuthToken = None  # type: ignore

try:
    from app.models import User  # type: ignore
except Exception:  # pragma: no cover
    User = Any  # type: ignore

router = APIRouter(prefix="/oauth", tags=["oauth"])


def _bool_env(val: str | None) -> bool:
    return bool(val and str(val).strip())


def _strava_configured() -> bool:
    return all(
        [
            _bool_env(settings.STRAVA_CLIENT_ID),
            _bool_env(settings.STRAVA_CLIENT_SECRET),
            _bool_env(settings.STRAVA_REDIRECT_URI),
        ]
    )


def _google_configured() -> bool:
    return all(
        [
            _bool_env(settings.GOOGLE_CLIENT_ID),
            _bool_env(settings.GOOGLE_CLIENT_SECRET),
            _bool_env(settings.GOOGLE_REDIRECT_URL),
        ]
    )


def _read_strava_link(db: Session, user_id: int) -> dict[str, Any]:
    """
    Returns a normalized status dict for Strava regardless of which token model you use.
    Looks for either:
      - app.models.StravaToken (fields like access_token, refresh_token, expires_at, athlete_id, scope)
      - app.models.OAuthToken with provider == 'strava'
    """
    linked = False
    athlete_id = None
    scope = None
    expires_at = None

    # Prefer a dedicated StravaToken model if present
    if _StravaToken is not None:
        row = (
            db.query(_StravaToken)  # type: ignore
            .filter(_StravaToken.user_id == user_id)  # type: ignore
            .first()
        )
        if row:
            linked = True
            # Try multiple common attribute names gracefully
            athlete_id = getattr(row, "athlete_id", None) or getattr(row, "external_athlete_id", None)
            scope = getattr(row, "scope", None)
            expires_at = getattr(row, "expires_at", None)

    # Fallback to a generic OAuthToken table
    elif _OAuthToken is not None:
        row = (
            db.query(_OAuthToken)  # type: ignore
            .filter(_OAuthToken.user_id == user_id)  # type: ignore
            .filter(_OAuthToken.provider == "strava")  # type: ignore
            .first()
        )
        if row:
            linked = True
            athlete_id = getattr(row, "external_athlete_id", None) or getattr(row, "athlete_id", None)
            scope = getattr(row, "scope", None)
            expires_at = getattr(row, "expires_at", None)

    return {
        "configured": _strava_configured(),
        "linked": bool(linked),
        "external_athlete_id": athlete_id,
        "scope": scope,
        "expires_at": expires_at,
    }


def _read_google_link(db: Session, user_id: int) -> dict[str, Any]:
    """
    Optional: report Google link if you store tokens in OAuthToken(provider='google') or a GoogleToken model.
    We keep it conservative: return configured status; linked only if we can detect a row.
    """
    linked = False
    sub = None
    expires_at = None
    email = None

    # Try generic OAuthToken(provider='google'), if your schema has it.
    if _OAuthToken is not None:
        row = (
            db.query(_OAuthToken)  # type: ignore
            .filter(_OAuthToken.user_id == user_id)  # type: ignore
            .filter(_OAuthToken.provider == "google")  # type: ignore
            .first()
        )
        if row:
            linked = True
            sub = getattr(row, "external_sub", None)
            expires_at = getattr(row, "expires_at", None)
            email = getattr(row, "email", None)

    return {
        "configured": _google_configured(),
        "linked": bool(linked),
        "external_sub": sub,
        "email": email,
        "expires_at": expires_at,
    }


@router.get("/status")
def oauth_status(
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Aggregated OAuth status for Profile page.

    Shape:
    {
      "strava": {
        "configured": true/false,
        "linked": true/false,
        "external_athlete_id": "12345" | null,
        "scope": "activity:read_all" | null,
        "expires_at": 1732849200 | null
      },
      "google": {
        "configured": true/false,
        "linked": true/false,
        "external_sub": "...",
        "email": "...",
        "expires_at": 1732849200 | null
      }
    }
    """
    if not current or not getattr(current, "id", None):
        raise HTTPException(status_code=401, detail="Not authenticated")

    return {
        "strava": _read_strava_link(db, int(current.id)),
        "google": _read_google_link(db, int(current.id)),
    }


@router.get("/linked")
def linked_providers(
    db: Session = Depends(get_db),
    current: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Simple list of linked providers (handy for debugging)."""
    if not current or not getattr(current, "id", None):
        raise HTTPException(status_code=401, detail="Not authenticated")

    s = _read_strava_link(db, int(current.id))
    g = _read_google_link(db, int(current.id))
    out = []
    if s.get("linked"):
        out.append("strava")
    if g.get("linked"):
        out.append("google")
    return {"linked": out}
