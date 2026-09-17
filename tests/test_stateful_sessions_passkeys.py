from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import PasskeyCredential, User, UserSession, WebAuthnChallenge
from app.rate_limit import AUTH_LIMITER, DistributedLimiter
from app.routers import operations, passkeys


@pytest.fixture()
def client_and_engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    def override_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    AUTH_LIMITER.clear()
    try:
        with TestClient(app) as client:
            yield client, engine
    finally:
        AUTH_LIMITER.clear()
        app.dependency_overrides.clear()


def test_sessions_are_individually_visible_and_revocable(client_and_engine):
    client, engine = client_and_engine
    assert (
        client.post(
            "/auth/signup", json={"email": "sessions@example.com", "password": "strong-session-passphrase"}
        ).status_code
        == 200
    )
    second = TestClient(app)
    assert (
        second.post(
            "/auth/login", json={"email": "sessions@example.com", "password": "strong-session-passphrase"}
        ).status_code
        == 200
    )

    listing = client.get("/auth/sessions")
    assert listing.status_code == 200
    sessions = listing.json()["sessions"]
    assert len(sessions) == 2
    assert sum(row["current"] for row in sessions) == 1

    other = next(row for row in sessions if not row["current"])
    assert client.delete(f"/auth/sessions/{other['id']}").status_code == 200
    assert second.get("/users/me").status_code == 401
    assert client.get("/users/me").status_code == 200
    with Session(engine) as db:
        assert db.query(UserSession).filter(UserSession.revoked_at.is_not(None)).count() == 1


def test_concurrent_session_limit_revokes_oldest(client_and_engine):
    client, engine = client_and_engine
    password = "bounded-session-passphrase"
    assert client.post("/auth/signup", json={"email": "bounded@example.com", "password": password}).status_code == 200
    clients = []
    for _ in range(5):
        other = TestClient(app)
        assert other.post("/auth/login", json={"email": "bounded@example.com", "password": password}).status_code == 200
        clients.append(other)
    assert client.get("/users/me").status_code == 401
    with Session(engine) as db:
        assert db.query(UserSession).filter(UserSession.revoked_at.is_(None)).count() == 5


def test_passkey_registration_login_and_replay_protection(client_and_engine, monkeypatch):
    client, engine = client_and_engine
    assert (
        client.post(
            "/auth/signup", json={"email": "passkey@example.com", "password": "secure-passkey-passphrase"}
        ).status_code
        == 200
    )
    credential_bytes = b"test-credential-id"
    credential_id = passkeys._b64(credential_bytes)
    monkeypatch.setattr(
        passkeys,
        "verify_registration_response",
        lambda **kwargs: SimpleNamespace(
            credential_id=credential_bytes,
            credential_public_key=b"public-key",
            sign_count=0,
            credential_device_type=SimpleNamespace(value="multi_device"),
            credential_backed_up=True,
        ),
    )
    options = client.post("/auth/passkeys/register/options")
    assert options.status_code == 200
    challenge_id = options.json()["challenge_id"]
    completed = client.post(
        "/auth/passkeys/register/complete",
        json={"challenge_id": challenge_id, "credential": {"id": credential_id}, "name": "My iPhone"},
    )
    assert completed.status_code == 200
    replay = client.post(
        "/auth/passkeys/register/complete",
        json={"challenge_id": challenge_id, "credential": {"id": credential_id}, "name": "Replay"},
    )
    assert replay.status_code == 400
    with Session(engine) as db:
        assert db.query(PasskeyCredential).one().name == "My iPhone"
        assert db.query(WebAuthnChallenge).one().used_at is not None

    monkeypatch.setattr(
        passkeys,
        "verify_authentication_response",
        lambda **kwargs: SimpleNamespace(
            new_sign_count=1,
            credential_device_type=SimpleNamespace(value="multi_device"),
            credential_backed_up=True,
        ),
    )
    client.cookies.clear()
    auth_options = client.post("/auth/passkeys/login/options")
    authenticated = client.post(
        "/auth/passkeys/login/complete",
        json={
            "challenge_id": auth_options.json()["challenge_id"],
            "credential": {"id": credential_id, "rawId": credential_id, "type": "public-key", "response": {}},
        },
    )
    assert authenticated.status_code == 200
    assert client.get("/users/me").status_code == 200
    assert client.get("/auth/sessions").json()["sessions"][0]["auth_method"] == "passkey"


def test_failed_passkey_verification_still_consumes_challenge(client_and_engine, monkeypatch):
    client, engine = client_and_engine
    assert (
        client.post(
            "/auth/signup",
            json={"email": "failed-passkey@example.com", "password": "secure-passkey-passphrase"},
        ).status_code
        == 200
    )

    def reject_registration(**kwargs):
        raise ValueError("invalid attestation")

    monkeypatch.setattr(passkeys, "verify_registration_response", reject_registration)
    options = client.post("/auth/passkeys/register/options")
    challenge_id = options.json()["challenge_id"]
    payload = {"challenge_id": challenge_id, "credential": {"id": "invalid"}, "name": "Invalid"}
    assert client.post("/auth/passkeys/register/complete", json=payload).status_code == 400
    assert client.post("/auth/passkeys/register/complete", json=payload).status_code == 400
    with Session(engine) as db:
        challenge = db.query(WebAuthnChallenge).one()
        assert challenge.used_at is not None


def test_expired_security_records_are_pruned_opportunistically(client_and_engine):
    client, engine = client_and_engine
    password = "retained-session-passphrase"
    assert client.post("/auth/signup", json={"email": "retention@example.com", "password": password}).status_code == 200
    stale_time = datetime.utcnow() - timedelta(days=31)
    with Session(engine) as db:
        user = db.query(User).filter(User.email == "retention@example.com").one()
        session = db.query(UserSession).filter(UserSession.user_id == user.id).one()
        session.expires_at = stale_time
        db.add(
            WebAuthnChallenge(
                user_id=user.id,
                challenge_hash=hashlib.sha256(b"stale-challenge").hexdigest(),
                challenge=b"stale",
                purpose="register",
                created_at=stale_time,
                expires_at=stale_time,
                used_at=stale_time,
            )
        )
        db.commit()

    fresh = TestClient(app)
    assert fresh.post("/auth/login", json={"email": "retention@example.com", "password": password}).status_code == 200
    assert fresh.post("/auth/passkeys/register/options").status_code == 200
    with Session(engine) as db:
        assert db.query(UserSession).filter(UserSession.expires_at == stale_time).count() == 0
        assert db.query(WebAuthnChallenge).filter(WebAuthnChallenge.challenge == b"stale").count() == 0


def test_admin_can_terminate_target_sessions(client_and_engine, monkeypatch):
    admin, engine = client_and_engine
    target = TestClient(app)
    monkeypatch.setattr(operations.settings, "ADMIN_EMAILS", "admin@example.com")
    assert (
        admin.post(
            "/auth/signup", json={"email": "admin@example.com", "password": "secure-admin-passphrase"}
        ).status_code
        == 200
    )
    assert (
        target.post(
            "/auth/signup", json={"email": "target@example.com", "password": "secure-target-passphrase"}
        ).status_code
        == 200
    )

    terminated = admin.post("/v1/operations/sessions/terminate", json={"email": "target@example.com"})
    assert terminated.status_code == 200
    assert terminated.json()["terminated"] == 1
    assert target.get("/users/me").status_code == 401
    with Session(engine) as db:
        assert db.query(UserSession).filter(UserSession.revoked_at.is_not(None)).count() == 1


def test_distributed_limiter_uses_atomic_backend(monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.current = 0

        def eval(self, script, key_count, key, window):
            assert "INCR" in script
            assert key_count == 1
            assert key.startswith("glycofy:rate:")
            assert window == "60"
            self.current += 1
            return [self.current, 60]

    limiter = DistributedLimiter()
    fake = FakeRedis()
    monkeypatch.setattr(limiter, "_redis", lambda: fake)
    limiter.check("login", maximum=1, window_seconds=60)
    with pytest.raises(Exception) as exc_info:
        limiter.check("login", maximum=1, window_seconds=60)
    assert getattr(exc_info.value, "status_code", None) == 429


def test_passkey_and_session_controls_are_present_in_ui() -> None:
    login = Path("ui/login.html").read_text(encoding="utf-8")
    login_script = Path("ui/login.js").read_text(encoding="utf-8")
    profile = Path("ui/profile.html").read_text(encoding="utf-8")
    profile_script = Path("ui/profile.js").read_text(encoding="utf-8")

    assert 'id="passkeyBtn"' in login
    assert "/auth/passkeys/login/options" in login_script
    assert 'id="add_passkey"' in profile
    assert 'id="session_list"' in profile
    assert "/auth/sessions/terminate-others" in profile_script
