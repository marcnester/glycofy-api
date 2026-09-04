# app/routers/auth.py
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.auth_utils import create_access_token, get_current_user
from app.config import settings
from app.db import get_db
from app.models import AccountActionToken, User
from app.observability import record_security_event
from app.rate_limit import AUTH_LIMITER, account_key, client_key
from app.services.account_email import account_email_configured, build_account_email_html, send_account_email

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


def _check_auth_limit(request: Request, action: str, identifier: str | None = None) -> None:
    maximum = settings.AUTH_RATE_LIMIT_PER_15_MINUTES
    AUTH_LIMITER.check(f"{action}:ip:{client_key(request)}", maximum=maximum, window_seconds=900)
    if identifier:
        AUTH_LIMITER.check(
            f"{action}:account:{account_key(identifier)}",
            maximum=maximum,
            window_seconds=900,
        )


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
    verification_sent: bool = False


class EmailRequest(BaseModel):
    email: EmailStr


class PasswordResetRequest(BaseModel):
    token: str = Field(min_length=32, max_length=256)
    password: str = Field(min_length=12, max_length=128)


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


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _issue_account_token(db: Session, user: User, purpose: str, hours: int) -> str:
    now = datetime.utcnow()
    db.query(AccountActionToken).filter(
        AccountActionToken.user_id == user.id,
        AccountActionToken.purpose == purpose,
        AccountActionToken.used_at.is_(None),
    ).update({"used_at": now})
    raw = secrets.token_urlsafe(32)
    db.add(
        AccountActionToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=_token_hash(raw),
            expires_at=now + timedelta(hours=hours),
            created_at=now,
        )
    )
    db.commit()
    return raw


def _account_link(path: str, token: str) -> str:
    return f"{(settings.PUBLIC_BASE_URL or '').rstrip('/')}{path}?token={token}"


def _send_verification(user: User, db: Session, background_tasks: BackgroundTasks) -> bool:
    if not account_email_configured():
        return False
    token = _issue_account_token(db, user, "verify_email", 24)
    verify_url = _account_link("/auth/verify-email", token)
    background_tasks.add_task(
        send_account_email,
        user.email,
        "Verify your Glycofy email",
        f"Verify your Glycofy email address:\n\n{verify_url}\n\nThis link expires in 24 hours.",
        build_account_email_html(
            preheader="Confirm your email to secure your Glycofy account.",
            heading="Welcome to Glycofy",
            message="Confirm your email address so we can keep your account secure and your training-fueled meal plans within reach.",
            action_label="Verify email address",
            action_url=verify_url,
            expires="This secure link expires in 24 hours.",
            security_note="If you did not create a Glycofy account, you can safely ignore this email.",
        ),
    )
    return True


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
def signup(request: Request, body: SignupRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    try:
        _check_auth_limit(request, "signup", body.email)
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

    token = _create_access_token(
        str(user.id), minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES, token_version=user.token_version
    )
    verification_sent = _send_verification(user, db, background_tasks)
    payload = {"ok": True}
    if verification_sent:
        payload["verification_sent"] = True
    resp = JSONResponse(payload)
    _set_all_session_cookies(resp, token)
    return resp


@router.post("/login", response_model=SessionResponse, summary="Log in and start a session")
def login(request: Request, body: LoginRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    try:
        _check_auth_limit(request, "login", body.email)
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

    token = _create_access_token(
        str(user.id), minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES, token_version=user.token_version
    )
    resp = JSONResponse({"ok": True})
    _set_all_session_cookies(resp, token)
    record_security_event(db, request, "authentication_login", "success", user_id=user.id)
    from app.routers.oauth_strava import schedule_strava_sync_on_login

    schedule_strava_sync_on_login(background_tasks=background_tasks, db=db, user_id=user.id)
    return resp


@router.post("/forgot-password")
def forgot_password(
    request: Request, body: EmailRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)
):
    _check_auth_limit(request, "forgot_password", body.email)
    user = db.query(User).filter(User.email == body.email.lower()).first()
    if user and account_email_configured():
        token = _issue_account_token(db, user, "reset_password", 1)
        reset_url = f"{_account_link('/ui/login.html', token)}&mode=reset"
        background_tasks.add_task(
            send_account_email,
            user.email,
            "Reset your Glycofy password",
            f"Reset your Glycofy password:\n\n{reset_url}\n\nThis link expires in one hour. If you did not request this, ignore this message.",
            build_account_email_html(
                preheader="Use this secure link to reset your Glycofy password.",
                heading="Reset your password",
                message="We received a request to reset your Glycofy password. Use the secure button below to choose a new one.",
                action_label="Reset password",
                action_url=reset_url,
                expires="This secure link expires in one hour and can only be used once.",
                security_note="If you did not request a password reset, no action is needed. Your password has not changed.",
            ),
        )
    return {"ok": True, "message": "If that account exists, reset instructions have been sent."}


@router.post("/reset-password")
def reset_password(request: Request, body: PasswordResetRequest, db: Session = Depends(get_db)):
    _check_auth_limit(request, "reset_password")
    now = datetime.utcnow()
    row = (
        db.query(AccountActionToken)
        .filter(
            AccountActionToken.token_hash == _token_hash(body.token),
            AccountActionToken.purpose == "reset_password",
            AccountActionToken.used_at.is_(None),
            AccountActionToken.expires_at > now,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired")
    user = db.query(User).filter(User.id == row.user_id).first()
    if not user:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired")
    user.password_hash = hash_password(body.password)
    user.token_version = int(user.token_version or 0) + 1
    row.used_at = now
    db.commit()
    record_security_event(db, request, "password_reset", "success", user_id=user.id)
    return {"ok": True}


@router.get("/verify-email")
def verify_email(token: str, db: Session = Depends(get_db)):
    now = datetime.utcnow()
    row = (
        db.query(AccountActionToken)
        .filter(
            AccountActionToken.token_hash == _token_hash(token),
            AccountActionToken.purpose == "verify_email",
            AccountActionToken.used_at.is_(None),
            AccountActionToken.expires_at > now,
        )
        .first()
    )
    if not row:
        return RedirectResponse("/ui/login.html?verification=invalid", status_code=303)
    user = db.query(User).filter(User.id == row.user_id).first()
    if not user:
        return RedirectResponse("/ui/login.html?verification=invalid", status_code=303)
    user.email_verified_at = now
    row.used_at = now
    db.commit()
    return RedirectResponse("/ui/login.html?verification=success", status_code=303)


@router.post("/resend-verification")
def resend_verification(
    background_tasks: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    if user.email_verified_at:
        return {"ok": True, "verification_sent": False}
    return {"ok": True, "verification_sent": _send_verification(user, db, background_tasks)}
