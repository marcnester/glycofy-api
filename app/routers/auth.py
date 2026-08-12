# app/routers/auth.py
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.auth_utils import create_access_token, get_current_user
from app.config import settings
from app.db import get_db
from app.models import User
from app.observability import record_security_event
from app.rate_limit import AUTH_LIMITER, client_key

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
def _create_access_token(sub: str, minutes: int = 60, token_version: int = 0) -> str:
    return create_access_token(sub, expires_minutes=minutes, token_version=token_version)


# -----------------------------------------------------------------------------
# Schemas
# -----------------------------------------------------------------------------
class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class SessionResponse(BaseModel):
    ok: bool = True


# -----------------------------------------------------------------------------
# Router / dependencies
# -----------------------------------------------------------------------------
router = APIRouter()

COOKIE_ACCESS = settings.SESSION_COOKIE_NAME
LEGACY_COOKIES = ("glyco_auth", "id_token", "glyco_token")


def _cookie_kwargs(*, http_only: bool):
    # Dev on 127.0.0.1 — secure=False + SameSite=Lax is fine.
    return dict(
        httponly=http_only,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


def _set_all_session_cookies(resp: JSONResponse, jwt_token: str) -> None:
    resp.set_cookie(COOKIE_ACCESS, jwt_token, **_cookie_kwargs(http_only=True))


def _clear_all_session_cookies(resp: JSONResponse) -> None:
    for name in (COOKIE_ACCESS, *LEGACY_COOKIES):
        resp.delete_cookie(name, path="/", samesite="lax")


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------
@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    user.token_version = int(user.token_version or 0) + 1
    db.add(user)
    db.commit()
    record_security_event(db, request, "session_logout", "success", user_id=user.id)
    resp = JSONResponse({"ok": True})
    _clear_all_session_cookies(resp)
    return resp


@router.post("/signup", response_model=SessionResponse, summary="Create account and start a session")
def signup(request: Request, body: SignupRequest, db: Session = Depends(get_db)):
    try:
        AUTH_LIMITER.check(
            f"signup:{client_key(request)}",
            maximum=settings.AUTH_RATE_LIMIT_PER_15_MINUTES,
            window_seconds=900,
        )
    except HTTPException:
        record_security_event(db, request, "signup_rate_limited", "denied", severity="alert")
        raise
    existing = db.query(User).filter(User.email == body.email.lower()).first()
    if existing:
        record_security_event(db, request, "account_signup", "denied", severity="warning")
        raise HTTPException(status_code=400, detail="email_in_use")

    user = User(email=body.email.lower(), password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    record_security_event(db, request, "account_signup", "success", user_id=user.id)

    token = _create_access_token(str(user.id), minutes=60, token_version=user.token_version)
    resp = JSONResponse(SessionResponse().model_dump())
    _set_all_session_cookies(resp, token)
    return resp


@router.post("/login", response_model=SessionResponse, summary="Log in and start a session")
def login(request: Request, body: LoginRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    try:
        AUTH_LIMITER.check(
            f"login:{client_key(request)}",
            maximum=settings.AUTH_RATE_LIMIT_PER_15_MINUTES,
            window_seconds=900,
        )
    except HTTPException:
        record_security_event(db, request, "authentication_rate_limited", "denied", severity="alert")
        raise
    user = db.query(User).filter(User.email == body.email.lower()).first()
    if not user or not verify_and_maybe_upgrade(user, body.password, db):
        record_security_event(
            db,
            request,
            "authentication_login",
            "failure",
            severity="warning",
            user_id=user.id if user else None,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")

    token = _create_access_token(str(user.id), minutes=60, token_version=user.token_version)
    resp = JSONResponse(SessionResponse().model_dump())
    _set_all_session_cookies(resp, token)
    record_security_event(db, request, "authentication_login", "success", user_id=user.id)
    from app.routers.oauth_strava import schedule_strava_sync_on_login

    schedule_strava_sync_on_login(background_tasks=background_tasks, db=db, user_id=user.id)
    return resp
