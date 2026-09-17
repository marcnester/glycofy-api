from __future__ import annotations

import hashlib
import secrets
import time
from datetime import datetime, timedelta

from fastapi import HTTPException, Request, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.models import User, UserSession


def session_hash(raw_session_id: str) -> str:
    return hashlib.sha256(raw_session_id.encode("utf-8")).hexdigest()


def _client_hash(request: Request) -> str | None:
    address = request.client.host if request.client else None
    if not address:
        return None
    return hashlib.sha256(f"{settings.JWT_SECRET}:{address}".encode()).hexdigest()


def device_label(request: Request) -> str:
    ua = (request.headers.get("user-agent") or "").lower()
    browser = "Browser"
    if "edg/" in ua:
        browser = "Edge"
    elif "firefox/" in ua:
        browser = "Firefox"
    elif "chrome/" in ua or "crios/" in ua:
        browser = "Chrome"
    elif "safari/" in ua:
        browser = "Safari"
    platform = "device"
    if "iphone" in ua:
        platform = "iPhone"
    elif "ipad" in ua:
        platform = "iPad"
    elif "android" in ua:
        platform = "Android"
    elif "mac os" in ua or "macintosh" in ua:
        platform = "Mac"
    elif "windows" in ua:
        platform = "Windows"
    elif "linux" in ua:
        platform = "Linux"
    return f"{browser} on {platform}"[:160]


def create_user_session(db: Session, request: Request, user: User, auth_method: str) -> str:
    now = datetime.utcnow()
    retention_cutoff = now - timedelta(days=settings.SESSION_RECORD_RETENTION_DAYS)
    db.query(UserSession).filter(
        UserSession.user_id == user.id,
        or_(UserSession.expires_at < retention_cutoff, UserSession.revoked_at < retention_cutoff),
    ).delete(synchronize_session=False)
    raw = secrets.token_urlsafe(32)
    row = UserSession(
        user_id=user.id,
        session_hash=session_hash(raw),
        auth_method=auth_method,
        device_label=device_label(request),
        client_id_hash=_client_hash(request),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(minutes=settings.SESSION_ABSOLUTE_TIMEOUT_MINUTES),
    )
    db.add(row)
    db.flush()
    active = (
        db.query(UserSession)
        .filter(
            UserSession.user_id == user.id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
        )
        .order_by(UserSession.created_at.desc(), UserSession.id.desc())
        .all()
    )
    for stale in active[settings.MAX_CONCURRENT_SESSIONS :]:
        stale.revoked_at = now
    db.commit()
    return raw


def require_active_session(db: Session, raw_session_id: str, user_id: int) -> UserSession:
    now = datetime.utcnow()
    row = (
        db.query(UserSession)
        .filter(
            UserSession.session_hash == session_hash(raw_session_id),
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session revoked")
    if row.last_seen_at < now - timedelta(minutes=5):
        row.last_seen_at = now
        db.commit()
    return row


def revoke_session(db: Session, raw_session_id: str, user_id: int) -> bool:
    row = (
        db.query(UserSession)
        .filter(UserSession.session_hash == session_hash(raw_session_id), UserSession.user_id == user_id)
        .first()
    )
    if not row or row.revoked_at:
        return False
    row.revoked_at = datetime.utcnow()
    db.commit()
    return True


def revoke_all_sessions(db: Session, user_id: int, *, except_raw_session_id: str | None = None) -> int:
    now = datetime.utcnow()
    query = db.query(UserSession).filter(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
    if except_raw_session_id:
        query = query.filter(UserSession.session_hash != session_hash(except_raw_session_id))
    count = query.update({"revoked_at": now}, synchronize_session=False)
    db.commit()
    return count


def require_recent_reauthentication(request: Request) -> None:
    payload = getattr(request.state, "session_payload", None) or {}
    auth_time = int(payload.get("auth_time") or 0)
    now_ts = int(time.time())
    if not auth_time or now_ts - auth_time > settings.SESSION_REAUTH_WINDOW_MINUTES * 60:
        raise HTTPException(status_code=403, detail="Please sign in again before changing account security settings.")
