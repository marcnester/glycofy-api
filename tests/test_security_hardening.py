from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base, get_db
from app.encrypted_types import EncryptedText
from app.main import app
from app.models import SecurityAuditEvent
from app.routers import oauth_strava


@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    def override_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def test_dashboard_requires_authentication(client: TestClient):
    response = client.get("/dashboard/today")
    assert response.status_code == 401
    assert response.headers["x-request-id"]


def test_dev_recipe_routes_are_not_mounted(client: TestClient):
    response = client.post("/dev/recipes/import", json={"recipes": []})
    assert response.status_code == 404


def test_cross_origin_mutation_is_rejected(client: TestClient):
    response = client.post("/auth/logout", headers={"Origin": "https://attacker.example"})
    assert response.status_code == 403


def test_oversized_request_is_rejected(client: TestClient):
    response = client.post(
        "/auth/login",
        content=b"{}",
        headers={"Content-Length": "2000000", "Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_session_cookie_is_http_only_and_response_has_no_token(client: TestClient):
    response = client.post(
        "/auth/signup",
        json={"email": "secure@example.com", "password": "a-secure-password-123"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    cookie = response.headers["set-cookie"].lower()
    assert "access_token=" in cookie
    assert "httponly" in cookie
    assert "glyco_token=" not in cookie


def test_logout_revokes_the_issued_session(client: TestClient):
    signup = client.post(
        "/auth/signup",
        json={"email": "logout@example.com", "password": "a-secure-password-456"},
    )
    assert signup.status_code == 200
    assert client.get("/users/me").status_code == 200
    assert client.post("/auth/logout").status_code == 200
    assert client.get("/users/me").status_code == 401


def test_authentication_events_are_persisted_without_raw_email(client: TestClient):
    client.post(
        "/auth/signup",
        json={"email": "audit@example.com", "password": "a-secure-password-789"},
    )
    client.post(
        "/auth/login",
        json={"email": "audit@example.com", "password": "incorrect-password"},
    )

    override = app.dependency_overrides[get_db]
    db = next(override())
    try:
        events = db.query(SecurityAuditEvent).order_by(SecurityAuditEvent.id).all()
        assert [(event.event_type, event.outcome) for event in events] == [
            ("account_signup", "success"),
            ("authentication_login", "failure"),
        ]
        assert all("audit@example.com" not in str(event.event_metadata) for event in events)
        assert all(event.request_id and event.client_id_hash for event in events)
    finally:
        db.close()


def test_strava_state_rejects_tampering_and_expiry(monkeypatch):
    state = oauth_strava._encode_state(42, "/ui/profile.html")
    assert oauth_strava._decode_state(state) == (42, "/ui/profile.html")
    with pytest.raises(ValueError):
        oauth_strava._decode_state(state[:-1] + ("A" if state[-1] != "A" else "B"))

    monkeypatch.setattr(oauth_strava, "_now_ts", lambda: int(time.time()) + 10_000)
    with pytest.raises(ValueError, match="expired_state"):
        oauth_strava._decode_state(state)


def test_oauth_tokens_are_encrypted_at_rest():
    encrypted_type = EncryptedText()
    stored = encrypted_type.process_bind_param("provider-secret", None)
    assert stored.startswith("gfy1:")
    assert "provider-secret" not in stored
    assert encrypted_type.process_result_value(stored, None) == "provider-secret"


def test_production_configuration_rejects_insecure_defaults():
    with pytest.raises(ValidationError, match="Unsafe production configuration"):
        Settings(_env_file=None, ENV="production")
