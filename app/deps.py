from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.auth_utils import decode_jwt
from app.db import get_db
from app.models import User  # adjust path if your models live elsewhere


def _unauthorized(msg: str) -> None:
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=msg)


def _extract_bearer(authorization: str | None) -> str:
    """
    Return the bearer token from an Authorization header.
    Always returns a str or raises 401.
    """
    if not authorization:
        _unauthorized("Missing Authorization header")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        _unauthorized("Invalid Authorization scheme")
    return parts[1]


def get_current_user(
    authorization: str | None = Header(default=None, convert_underscores=True),
    db: Session = Depends(get_db),
) -> User:
    """
    Decode JWT, look up user, and return a concrete User model instance.
    Never returns None; raises 401 on any auth failure.
    """
    token = _extract_bearer(authorization)

    try:
        payload = decode_jwt(token)
    except Exception:
        _unauthorized("Invalid token")

    sub = (payload or {}).get("sub")
    if not sub:
        _unauthorized("Invalid token subject")

    # Match by user-sub OR email (adjust to your schema/columns)
    user: User | None = (
        db.query(User)  # type: ignore[attr-defined]
        .filter((User.sub == sub) | (User.email == sub))  # type: ignore[attr-defined]
        .first()
    )
    if not user:
        _unauthorized("Not authenticated")
    return user
