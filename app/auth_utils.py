# app/auth_utils.py
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt  # PyJWT
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User

# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------
JWT_SECRET: str = getattr(settings, "JWT_SECRET", os.getenv("JWT_SECRET", "dev-secret"))
JWT_ALGORITHM: str = getattr(settings, "JWT_ALGORITHM", os.getenv("JWT_ALGORITHM", "HS256"))
ACCESS_TOKEN_EXPIRE_MINUTES: int = int(
    getattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
)

bearer_scheme = HTTPBearer(auto_error=False)  # allow missing header so we can fall back to cookie


# ----------------------------------------------------------------------
# Token creation & decoding
# ----------------------------------------------------------------------
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(sub: str | int, minutes: Optional[int] = None) -> str:
    """Issue a signed JWT with subject = user id. Adds iat/exp claims."""
    if minutes is None:
        minutes = ACCESS_TOKEN_EXPIRE_MINUTES

    now = _now_utc()
    exp = now + timedelta(minutes=minutes)
    payload = {
        "sub": str(sub),
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _token_from_request(request: Request, cred: HTTPAuthorizationCredentials | None) -> Optional[str]:
    """Prefer Authorization header; else fall back to HttpOnly cookie 'access_token'."""
    if cred and cred.scheme.lower() == "bearer" and cred.credentials:
        return cred.credentials
    # Fallback to cookie
    token = request.cookies.get("access_token")
    return token


# ----------------------------------------------------------------------
# Current user dependency (supports header or cookie)
# ----------------------------------------------------------------------
def get_current_user(
    request: Request,
    cred: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    token = _token_from_request(request, cred)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    payload = decode_token(token)
    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")

    user = db.query(User).filter(User.id == int(sub)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user