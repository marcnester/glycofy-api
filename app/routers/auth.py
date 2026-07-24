# app/routers/auth.py
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
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
try:
    from passlib.context import CryptContext
except Exception as e:
    raise RuntimeError("passlib is required. Install with: pip install 'passlib[bcrypt]'") from e

pwd_context = CryptContext(
    schemes=["bcrypt", "bcrypt_sha256", "pbkdf2_sha256"],
    deprecated="auto",
)


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_and_maybe_upgrade(user: User, plain: str, db: Session) -> bool:
    if not user.password_hash:
        return False
    try:
        verified = pwd_context.verify(plain, user.password_hash)
    except Exception:
        return False
    if verified and pwd_context.needs_update(user.password_hash):
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

# Optional bearer; we also fall back to cookies.
bearer_scheme = HTTPBearer(auto_error=False)

# Cookie names (keep legacy + add id_token + SPA-readable)
COOKIE_ACCESS = "access_token"  # HttpOnly
COOKIE_LEGACY = "glyco_auth"  # HttpOnly (back-compat)
COOKIE_ID = "id_token"  # HttpOnly (for deps that expect this)
COOKIE_SPA = "glyco_token"  # readable by JS (non-HttpOnly)


def _cookie_kwargs(*, http_only: bool):
    # Dev on 127.0.0.1 — secure=False + SameSite=Lax is fine.
    return dict(
        httponly=http_only,
        secure=False,
        samesite="lax",
        path="/",
        max_age=60 * 60 * 24 * 14,  # 14 days
    )


def _set_all_session_cookies(resp: JSONResponse, jwt_token: str) -> None:
    # HttpOnly cookies (server-side auth)
    resp.set_cookie(COOKIE_ACCESS, jwt_token, **_cookie_kwargs(http_only=True))
    resp.set_cookie(COOKIE_LEGACY, jwt_token, **_cookie_kwargs(http_only=True))
    resp.set_cookie(COOKIE_ID, jwt_token, **_cookie_kwargs(http_only=True))
    # SPA-readable mirror for the UI, if you want to read it in JS
    resp.set_cookie(COOKIE_SPA, jwt_token, **_cookie_kwargs(http_only=False))


def _clear_all_session_cookies(resp: JSONResponse) -> None:
    for name in (COOKIE_ACCESS, COOKIE_LEGACY, COOKIE_ID, COOKIE_SPA):
        resp.delete_cookie(name, path="/", samesite="lax")


def _extract_bearer_token(credentials: HTTPAuthorizationCredentials | None) -> str | None:
    if not credentials:
        return None
    if credentials.scheme.lower() != "bearer":
        return None
    return credentials.credentials or None


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    # 1) Bearer header?
    token = _extract_bearer_token(credentials)

    # 2) Otherwise, look in cookies (we now set access_token, glyco_auth, id_token)
    if not token:
        token = (
            request.cookies.get(COOKIE_ACCESS) or request.cookies.get(COOKIE_LEGACY) or request.cookies.get(COOKIE_ID)
        )

    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing credentials")

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
@router.post("/logout")
def logout():
    resp = JSONResponse({"ok": True})
    _clear_all_session_cookies(resp)
    return resp


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
    resp = JSONResponse(TokenResponse(access_token=token).model_dump())
    _set_all_session_cookies(resp, token)
    return resp


@router.post("/login", response_model=TokenResponse, summary="Log in and return JWT")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email.lower()).first()
    if not user or not verify_and_maybe_upgrade(user, body.password, db):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")

    token = _create_access_token(str(user.id), minutes=60)
    resp = JSONResponse(TokenResponse(access_token=token).model_dump())
    _set_all_session_cookies(resp, token)
    return resp
