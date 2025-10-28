# app/deps.py
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import User


# --- DB session dependency ----------------------------------------------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- Auth dependency: current user from Bearer token --------------------------
def _jwt_secret_and_alg() -> tuple[str, str]:
    """
    Try common setting names to remain compatible with your existing auth code.
    """
    secret: Optional[str] = getattr(settings, "JWT_SECRET", None) or getattr(settings, "SECRET_KEY", None)
    if not secret:
        raise RuntimeError("JWT secret not configured (expected settings.JWT_SECRET or settings.SECRET_KEY)")
    alg: str = getattr(settings, "JWT_ALG", "HS256")
    return secret, alg


def _unauthorized(detail: str = "Not authenticated"):
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def parse_bearer_token(authorization: Optional[str]) -> str:
    if not authorization:
        _unauthorized("Missing Authorization header")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        _unauthorized("Invalid Authorization scheme")
    return parts[1]


def decode_user_id(token: str) -> int:
    secret, alg = _jwt_secret_and_alg()
    try:
        payload = jwt.decode(token, secret, algorithms=[alg])
    except JWTError:
        _unauthorized("Invalid token")
    sub = payload.get("sub")
    if sub is None:
        _unauthorized("Invalid token payload")
    try:
        return int(sub)
    except (TypeError, ValueError):
        _unauthorized("Invalid token subject")


def get_current_user(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> User:
    """
    Extracts the Bearer token, decodes it using your configured secret/algorithm,
    and returns the User from the database.
    """
    token = parse_bearer_token(authorization)
    user_id = decode_user_id(token)
    user = db.query(User).get(user_id)  # SQLAlchemy 1.4 compatible
    if not user:
        _unauthorized("User not found")
    return user