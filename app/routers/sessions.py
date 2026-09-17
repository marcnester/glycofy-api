from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth_utils import get_current_user
from app.db import get_db
from app.models import User, UserSession
from app.observability import record_security_event
from app.services.user_sessions import (
    require_recent_reauthentication,
    revoke_all_sessions,
    session_hash,
)

router = APIRouter()


def _current_sid(request: Request) -> str:
    return str((getattr(request.state, "session_payload", None) or {}).get("sid") or "")


@router.get("")
def list_sessions(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    now = datetime.utcnow()
    current_hash = session_hash(_current_sid(request)) if _current_sid(request) else None
    rows = (
        db.query(UserSession)
        .filter(UserSession.user_id == user.id, UserSession.revoked_at.is_(None), UserSession.expires_at > now)
        .order_by(UserSession.last_seen_at.desc())
        .all()
    )
    return {
        "sessions": [
            {
                "id": row.id,
                "device": row.device_label,
                "auth_method": row.auth_method,
                "created_at": row.created_at.isoformat() + "Z",
                "last_seen_at": row.last_seen_at.isoformat() + "Z",
                "current": row.session_hash == current_hash,
            }
            for row in rows
        ]
    }


@router.delete("/{session_id}")
def terminate_session(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_recent_reauthentication(request)
    row = db.query(UserSession).filter(UserSession.id == session_id, UserSession.user_id == user.id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    row.revoked_at = datetime.utcnow()
    db.commit()
    record_security_event(db, request, "session_terminated", "success", user_id=user.id)
    return {"ok": True, "current": row.session_hash == session_hash(_current_sid(request))}


@router.post("/terminate-others")
def terminate_other_sessions(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_recent_reauthentication(request)
    count = revoke_all_sessions(db, user.id, except_raw_session_id=_current_sid(request) or None)
    record_security_event(db, request, "sessions_terminated", "success", user_id=user.id, details={"count": count})
    return {"ok": True, "terminated": count}
