# app/auth_utils.py
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User

# ---------------------------
# Password utilities
# ---------------------------


def hash_password(plain: str) -> str:
    if not isinstance(plain, str):
        raise ValueError("password must be a string")
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


# ---------------------------
# JWT utilities
# ---------------------------

# Our app issues HS256 tokens. Enforce HS256 strictly to avoid "none"/RS256 leaks.
ALG = "HS256"
ALLOWED_ALGS = ["HS256"]


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def create_access_token(subject: str | int, expires_minutes: int | None = None, token_version: int = 0) -> str:
    if expires_minutes is None:
        expires_minutes = int(getattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", 60) or 60)

    now = _now_utc()
    payload = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expires_minutes)).timestamp()),
        "iss": settings.JWT_ISS,
        "aud": settings.JWT_AUD,
        "ver": int(token_version),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALG)


def _decode_token_internal(token: str) -> dict:
    """
    Decode only app-minted HS256 tokens. Any other alg/shape yields generic 401.
    """
    try:
        return jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=ALLOWED_ALGS,
            issuer=settings.JWT_ISS,
            audience=settings.JWT_AUD,
            options={"require": ["sub", "exp", "iat", "iss", "aud", "ver"], "verify_signature": True},
        )
    except jwt.ExpiredSignatureError:
        # Session expired is a useful, specific message.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    except (jwt.InvalidAlgorithmError, jwt.InvalidSignatureError, jwt.DecodeError, jwt.InvalidTokenError):
        # Avoid leaking "alg not allowed" etc.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


# Public helper to keep compatibility
def decode_jwt(token: str) -> dict:
    return _decode_token_internal(token)


def _looks_like_jwt(val: str | None) -> bool:
    return bool(val) and val.count(".") == 2


def _header_alg(token: str) -> str | None:
    """
    Peek at the JWT header 'alg' without verifying signature, to filter RS256/id_token, etc.
    """
    try:
        # decode header only; verify=False is okay here since we do *not* trust this result,
        # just using it to filter which token to attempt to verify later.
        header = jwt.get_unverified_header(token)
        return header.get("alg")
    except Exception:
        return None


def _pick_app_token(request: Request) -> str | None:
    """
    Prefer app-issued JWTs only:
      1) access_token (HttpOnly)
      2) Authorization: Bearer <jwt>
    Explicitly ignore Google id_token (RS256) and any non-HS256 token.
    """
    cookies = request.cookies or {}

    # Browser session cookie.
    t = (cookies.get(settings.SESSION_COOKIE_NAME) or "").strip()
    if _looks_like_jwt(t) and _header_alg(t) in ALLOWED_ALGS:
        return t

    # Non-browser API client.
    auth = request.headers.get("Authorization") or request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        bearer = auth.split(" ", 1)[1].strip()
        if _looks_like_jwt(bearer) and _header_alg(bearer) in ALLOWED_ALGS:
            return bearer

    # Never consider id_token here (Google RS256)
    return None


# ---------------------------
# FastAPI dependency
# ---------------------------


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """
    Current user must be identified by our app's HS256 JWT — either from
    glyco_token/access_token cookies or Authorization: Bearer header.
    """
    token = _pick_app_token(request)
    if not token:
        # Clear, generic message; avoids "Not enough segments" or alg noise
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    payload = _decode_token_internal(token)
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        uid = int(sub)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    user = db.query(User).filter(User.id == uid).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    if int(payload.get("ver", -1)) != int(user.token_version or 0):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session revoked")

    return user
