# app/routers/auth.py
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User

# -----------------------------------------------------------------------------
# Password hashing
# -----------------------------------------------------------------------------
# Accept common legacy hashes for verification, but prefer bcrypt for new hashes.
try:
    from passlib.context import CryptContext
except Exception as e:
    raise RuntimeError("passlib is required. Install with: pip install 'passlib[bcrypt]'") from e

# Order matters: first scheme is the default for new hashes.
pwd_context = CryptContext(
    schemes=["bcrypt", "bcrypt_sha256", "pbkdf2_sha256"],
    deprecated="auto",
)


def hash_password(plain: str) -> str:
    # Always create new hashes with bcrypt (first in schemes)
    return pwd_context.hash(plain)


def verify_and_maybe_upgrade(user: User, plain: str, db: Session) -> bool:
    """
    Verify password against user's stored hash (supporting legacy formats).
    If the hash is valid but outdated, upgrade it to the current default.
    """
    if not user.password_hash:
        return False
    try:
        verified = pwd_context.verify(plain, user.password_hash)
    except Exception:
        # Unknown/invalid hash format
        return False

    if verified and pwd_context.needs_update(user.password_hash):
        # Upgrade hash to current default (bcrypt)
        user.password_hash = hash_password(plain)
        db.add(user)
        db.commit()
    return verified


# -----------------------------------------------------------------------------
# JWT
# -----------------------------------------------------------------------------
JWT_SECRET = getattr(settings, "JWT_SECRET", None) or "dev-insecure-secret-change-me"
JWT_ALG = "HS256"


def _create_access_token(sub: str, minutes: int = 60) -> str:
    now = datetime.now(tz=UTC)
    payload = {"sub": sub, "exp": now + timedelta(minutes=minutes), "iat": now}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def _decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


# -----------------------------------------------------------------------------
# Schemas
# -----------------------------------------------------------------------------
class SignupRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# -----------------------------------------------------------------------------
# Router / dependencies
# -----------------------------------------------------------------------------
router = APIRouter()


@router.post("/logout")
def logout():
    resp = JSONResponse({"ok": True})
    # Delete session cookie
    resp.delete_cookie("access_token", path="/")
    return resp


bearer_scheme = HTTPBearer(auto_error=False)


def _extract_bearer_token(
    credentials: HTTPAuthorizationCredentials | None,
) -> str | None:
    if not credentials:
        return None
    if credentials.scheme.lower() != "bearer":
        return None
    return credentials.credentials or None


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    token = _extract_bearer_token(credentials)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    payload = _decode_token(token)
    sub = str(payload.get("sub") or "")
    if not sub.isdigit():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user = db.query(User).filter(User.id == int(sub)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return user


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------
@router.post("/signup", response_model=TokenResponse, summary="Create account and return JWT")
def signup(body: SignupRequest, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == body.email.lower()).first()
    if existing:
        raise HTTPException(status_code=400, detail="email_in_use")

    user = User(email=body.email.lower(), password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)

    token = _create_access_token(str(user.id), minutes=60)
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse, summary="Log in and return JWT")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email.lower()).first()
    if not user or not verify_and_maybe_upgrade(user, body.password, db):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")
    token = _create_access_token(str(user.id), minutes=60)
    return TokenResponse(access_token=token)
