from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session
from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.auth_utils import get_current_user
from app.config import settings
from app.db import get_db
from app.models import PasskeyCredential, User, WebAuthnChallenge
from app.observability import record_security_event
from app.rate_limit import AUTH_LIMITER, client_key
from app.routers.auth import _create_access_token, _set_all_session_cookies
from app.services.account_email import account_email_configured, build_account_email_html, send_account_email
from app.services.user_sessions import create_user_session, require_recent_reauthentication

router = APIRouter()


class PasskeyFinish(BaseModel):
    challenge_id: str = Field(min_length=32, max_length=256)
    credential: dict[str, Any]
    name: str = Field(default="Passkey", min_length=1, max_length=80)


class PasskeyLoginFinish(BaseModel):
    challenge_id: str = Field(min_length=32, max_length=256)
    credential: dict[str, Any]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _challenge_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _issue_challenge(db: Session, purpose: str, user_id: int | None = None) -> tuple[str, bytes]:
    now = datetime.utcnow()
    retention_cutoff = now - timedelta(hours=settings.WEBAUTHN_CHALLENGE_RETENTION_HOURS)
    db.query(WebAuthnChallenge).filter(
        or_(
            WebAuthnChallenge.expires_at < retention_cutoff,
            WebAuthnChallenge.used_at < retention_cutoff,
        )
    ).delete(synchronize_session=False)
    raw_id = secrets.token_urlsafe(32)
    challenge = secrets.token_bytes(32)
    db.add(
        WebAuthnChallenge(
            user_id=user_id,
            challenge_hash=_challenge_hash(raw_id),
            challenge=challenge,
            purpose=purpose,
            created_at=now,
            expires_at=now + timedelta(seconds=settings.WEBAUTHN_CHALLENGE_TTL_SECONDS),
        )
    )
    db.commit()
    return raw_id, challenge


def _consume_challenge(db: Session, raw_id: str, purpose: str, user_id: int | None = None) -> WebAuthnChallenge:
    now = datetime.utcnow()
    query = db.query(WebAuthnChallenge).filter(
        WebAuthnChallenge.challenge_hash == _challenge_hash(raw_id),
        WebAuthnChallenge.purpose == purpose,
        WebAuthnChallenge.used_at.is_(None),
        WebAuthnChallenge.expires_at > now,
    )
    if user_id is not None:
        query = query.filter(WebAuthnChallenge.user_id == user_id)
    row = query.with_for_update().first()
    if not row:
        raise HTTPException(status_code=400, detail="This passkey request expired or was already used.")
    row.used_at = now
    # Consume before invoking the authenticator verifier. A malformed response
    # must not make the challenge reusable after the verification transaction
    # rolls back.
    db.commit()
    return row


def _notify_passkey_change(background_tasks: BackgroundTasks, user: User, action: str) -> None:
    if not account_email_configured():
        return
    background_tasks.add_task(
        send_account_email,
        user.email,
        f"A passkey was {action} on your Glycofy account",
        f"A passkey was {action} on your Glycofy account. If this was not you, reset your password and contact support.",
        build_account_email_html(
            preheader=f"Account security notice: passkey {action}.",
            heading=f"Passkey {action}",
            message=f"A passkey was {action} on your Glycofy account.",
            action_label="Review account security",
            action_url=f"{(settings.PUBLIC_BASE_URL or '').rstrip('/')}/ui/profile.html",
            expires="This is a security notification; no action is needed if you made this change.",
            security_note="If this was not you, reset your password immediately and contact Glycofy support.",
        ),
    )


@router.get("")
def list_passkeys(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = (
        db.query(PasskeyCredential)
        .filter(PasskeyCredential.user_id == user.id, PasskeyCredential.revoked_at.is_(None))
        .order_by(PasskeyCredential.created_at.desc())
        .all()
    )
    return {
        "passkeys": [
            {
                "id": row.id,
                "name": row.name,
                "created_at": row.created_at.isoformat() + "Z",
                "last_used_at": row.last_used_at.isoformat() + "Z" if row.last_used_at else None,
                "backed_up": row.backed_up,
            }
            for row in rows
        ]
    }


@router.post("/register/options")
def registration_options(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_recent_reauthentication(request)
    raw_id, challenge = _issue_challenge(db, "register", user.id)
    credentials = (
        db.query(PasskeyCredential)
        .filter(PasskeyCredential.user_id == user.id, PasskeyCredential.revoked_at.is_(None))
        .all()
    )
    options = generate_registration_options(
        rp_id=settings.WEBAUTHN_RP_ID,
        rp_name=settings.WEBAUTHN_RP_NAME,
        user_id=str(user.id).encode(),
        user_name=user.email,
        user_display_name=user.display_name or user.email,
        challenge=challenge,
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(row.credential_id)) for row in credentials
        ],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            require_resident_key=True,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    return {"challenge_id": raw_id, "publicKey": json.loads(options_to_json(options))}


@router.post("/register/complete")
def complete_registration(
    body: PasskeyFinish,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_recent_reauthentication(request)
    challenge = _consume_challenge(db, body.challenge_id, "register", user.id)
    try:
        verified = verify_registration_response(
            credential=body.credential,
            expected_challenge=challenge.challenge,
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=settings.WEBAUTHN_EXPECTED_ORIGIN,
            require_user_verification=True,
        )
    except Exception as exc:
        db.rollback()
        record_security_event(db, request, "passkey_registration", "failure", severity="warning", user_id=user.id)
        raise HTTPException(status_code=400, detail="Passkey registration could not be verified.") from exc
    credential_id = _b64(verified.credential_id)
    if db.query(PasskeyCredential).filter(PasskeyCredential.credential_id == credential_id).first():
        db.rollback()
        raise HTTPException(status_code=409, detail="This passkey is already registered.")
    row = PasskeyCredential(
        user_id=user.id,
        credential_id=credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        device_type=str(verified.credential_device_type.value),
        backed_up=verified.credential_backed_up,
        name=body.name.strip() or "Passkey",
    )
    db.add(row)
    db.commit()
    _notify_passkey_change(background_tasks, user, "added")
    record_security_event(db, request, "passkey_registration", "success", user_id=user.id)
    return {"ok": True, "id": row.id}


@router.post("/login/options")
def authentication_options(request: Request, db: Session = Depends(get_db)):
    AUTH_LIMITER.check(
        f"passkey-login:ip:{client_key(request)}",
        maximum=settings.AUTH_RATE_LIMIT_PER_15_MINUTES,
        window_seconds=900,
    )
    raw_id, challenge = _issue_challenge(db, "authenticate")
    options = generate_authentication_options(
        rp_id=settings.WEBAUTHN_RP_ID,
        challenge=challenge,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return {"challenge_id": raw_id, "publicKey": json.loads(options_to_json(options))}


@router.post("/login/complete")
def complete_authentication(body: PasskeyLoginFinish, request: Request, db: Session = Depends(get_db)):
    AUTH_LIMITER.check(
        f"passkey-login:ip:{client_key(request)}",
        maximum=settings.AUTH_RATE_LIMIT_PER_15_MINUTES,
        window_seconds=900,
    )
    challenge = _consume_challenge(db, body.challenge_id, "authenticate")
    credential_id = str(body.credential.get("id") or "")
    row = (
        db.query(PasskeyCredential)
        .filter(PasskeyCredential.credential_id == credential_id, PasskeyCredential.revoked_at.is_(None))
        .first()
    )
    if not row:
        db.rollback()
        raise HTTPException(status_code=401, detail="Passkey sign-in failed.")
    user = db.query(User).filter(User.id == row.user_id).first()
    if not user:
        db.rollback()
        raise HTTPException(status_code=401, detail="Passkey sign-in failed.")
    try:
        verified = verify_authentication_response(
            credential=body.credential,
            expected_challenge=challenge.challenge,
            expected_rp_id=settings.WEBAUTHN_RP_ID,
            expected_origin=settings.WEBAUTHN_EXPECTED_ORIGIN,
            credential_public_key=row.public_key,
            credential_current_sign_count=row.sign_count,
            require_user_verification=True,
        )
    except Exception as exc:
        db.rollback()
        record_security_event(db, request, "passkey_authentication", "failure", severity="warning")
        raise HTTPException(status_code=401, detail="Passkey sign-in failed.") from exc
    row.sign_count = verified.new_sign_count
    row.last_used_at = datetime.utcnow()
    row.device_type = str(verified.credential_device_type.value)
    row.backed_up = verified.credential_backed_up
    db.commit()
    session_id = create_user_session(db, request, user, "passkey")
    token = _create_access_token(str(user.id), token_version=user.token_version, session_id=session_id)
    response = JSONResponse({"ok": True})
    _set_all_session_cookies(response, token)
    record_security_event(db, request, "passkey_authentication", "success", user_id=user.id)
    return response


@router.delete("/{passkey_id}")
def delete_passkey(
    passkey_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_recent_reauthentication(request)
    row = (
        db.query(PasskeyCredential)
        .filter(
            PasskeyCredential.id == passkey_id,
            PasskeyCredential.user_id == user.id,
            PasskeyCredential.revoked_at.is_(None),
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Passkey not found")
    row.revoked_at = datetime.utcnow()
    db.commit()
    _notify_passkey_change(background_tasks, user, "removed")
    record_security_event(db, request, "passkey_removal", "success", user_id=user.id)
    return {"ok": True}
