from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import DateTime, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth_utils import create_access_token, decode_jwt
from app.config import Settings, settings
from app.db import Base, get_db
from app.encrypted_types import EncryptedText
from app.main import app
from app.models import Activity, OAuthAccount, SecurityAuditEvent, User
from app.rate_limit import AUTH_LIMITER, account_key
from app.routers import oauth_google, oauth_strava


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


@pytest.fixture(autouse=True)
def reset_auth_limiter():
    AUTH_LIMITER.clear()
    yield
    AUTH_LIMITER.clear()


def test_dashboard_requires_authentication(client: TestClient):
    response = client.get("/dashboard/today")
    assert response.status_code == 401
    assert response.headers["x-request-id"]


def test_dev_recipe_routes_are_not_mounted(client: TestClient):
    response = client.post("/dev/recipes/import", json={"recipes": []})
    assert response.status_code == 404


def test_login_page_does_not_expose_demo_credentials(client: TestClient):
    response = client.get("/ui/login.html")
    assert response.status_code == 200
    assert "demo credentials" not in response.text.lower()
    assert "demo@glycofy.app" not in response.text.lower()


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


def test_session_expiry_is_enforced_and_cookie_matches_config(client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", 7)
    response = client.post(
        "/auth/signup",
        json={"email": "expiry@example.com", "password": "a-secure-password-456"},
    )
    assert response.status_code == 200
    assert "Max-Age=420" in response.headers["set-cookie"]
    payload = decode_jwt(client.cookies.get(settings.SESSION_COOKIE_NAME))
    assert payload["exp"] - payload["iat"] == 420

    expired = create_access_token("1", expires_minutes=-1)
    with pytest.raises(HTTPException, match="Session expired"):
        decode_jwt(expired)


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


def test_strava_scope_parser_requires_private_activity_read():
    assert oauth_strava._scope_values("read,activity:read_all") == {"read", "activity:read_all"}
    assert "activity:read_all" not in oauth_strava._scope_values("read,activity:read")


def test_strava_disconnect_revokes_provider_and_deletes_tokens(client: TestClient, monkeypatch):
    assert (
        client.post(
            "/auth/signup",
            json={"email": "strava-disconnect@example.com", "password": "a-secure-password-789"},
        ).status_code
        == 200
    )
    override = app.dependency_overrides[get_db]
    db = next(override())
    try:
        user = db.query(User).filter(User.email == "strava-disconnect@example.com").one()
        db.add(
            OAuthAccount(
                user_id=user.id,
                provider="strava",
                external_athlete_id="athlete-123",
                access_token="provider-access-token",
                refresh_token="provider-refresh-token",
                expires_at=int(time.time()) + 3600,
                scope="read,activity:read_all",
            )
        )
        db.commit()
    finally:
        db.close()

    calls = []

    class FakeResponse:
        status_code = 200

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    monkeypatch.setattr(oauth_strava.requests, "post", fake_post)
    response = client.post("/oauth/strava/disconnect")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "provider_revoked": True}
    assert calls[0][0] == "https://www.strava.com/oauth/deauthorize"
    db = next(override())
    try:
        assert db.query(OAuthAccount).filter(OAuthAccount.provider == "strava").count() == 0
    finally:
        db.close()


def test_google_state_rejects_tampering_and_expiry(monkeypatch):
    state = oauth_google._encode_state()
    oauth_google._decode_state(state)
    with pytest.raises(ValueError, match="bad_state"):
        oauth_google._decode_state(state[:-1] + ("A" if state[-1] != "A" else "B"))

    monkeypatch.setattr(oauth_google, "_now_ts", lambda: int(time.time()) + 10_000)
    with pytest.raises(ValueError, match="expired_state"):
        oauth_google._decode_state(state)


def test_password_login_is_rate_limited_by_ip_and_account(client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_PER_15_MINUTES", 2)
    payload = {"email": "target@example.com", "password": "incorrect-password"}

    assert client.post("/auth/login", json=payload).status_code == 401
    assert client.post("/auth/login", json=payload).status_code == 401
    blocked = client.post("/auth/login", json=payload)

    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0
    assert "target@example.com" not in account_key(payload["email"])


def test_google_oauth_start_is_rate_limited(client: TestClient, monkeypatch):
    monkeypatch.setattr(oauth_google, "GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr(oauth_google, "GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(oauth_google, "GOOGLE_REDIRECT_URL", "https://app.glycofy.ai/oauth/google/callback")
    monkeypatch.setattr(settings, "OAUTH_RATE_LIMIT_PER_15_MINUTES", 1)

    assert client.get("/oauth/google/start", follow_redirects=False).status_code == 302
    blocked = client.get("/oauth/google/start", follow_redirects=False)
    assert blocked.status_code == 429


def test_google_start_uses_login_scopes_without_offline_access(client: TestClient, monkeypatch):
    monkeypatch.setattr(oauth_google, "GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr(oauth_google, "GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(oauth_google, "GOOGLE_REDIRECT_URL", "https://app.glycofy.ai/oauth/google/callback")

    response = client.get("/oauth/google/start?return=/ui/profile.html", follow_redirects=False)

    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["scope"] == ["openid email profile"]
    assert "access_type" not in query
    assert "include_granted_scopes" not in query
    assert client.cookies.get(oauth_google.RETURN_COOKIE_NAME).strip('"') == "/ui/profile.html"


def test_google_callback_rejects_unverified_email(client: TestClient, monkeypatch):
    monkeypatch.setattr(oauth_google, "GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr(oauth_google, "GOOGLE_CLIENT_SECRET", "client-secret")
    monkeypatch.setattr(oauth_google, "GOOGLE_REDIRECT_URL", "https://app.glycofy.ai/oauth/google/callback")
    google_profile = {"sub": "google-subject", "email": "person@example.com", "email_verified": False}

    class FakeResponse:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload

        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return FakeResponse({"access_token": "temporary-token", "scope": "openid email profile"})

        async def get(self, *args, **kwargs):
            return FakeResponse(google_profile)

    monkeypatch.setattr(oauth_google.httpx, "AsyncClient", FakeAsyncClient)
    start = client.get("/oauth/google/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

    response = client.get(f"/oauth/google/callback?code=test-code&state={state}")

    assert response.status_code == 400
    assert response.json()["detail"] == "Google email is not verified"
    override = app.dependency_overrides[get_db]
    db = next(override())
    try:
        assert db.query(User).count() == 0
        assert db.query(OAuthAccount).count() == 0
    finally:
        db.close()

    google_profile["email_verified"] = True
    start = client.get("/oauth/google/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    response = client.get(f"/oauth/google/callback?code=test-code&state={state}", follow_redirects=False)

    assert response.status_code == 302
    db = next(override())
    try:
        account = db.query(OAuthAccount).one()
        assert account.external_athlete_id == "google-subject"
        assert account.access_token is None
        assert account.refresh_token is None
        assert account.created_at is not None
        assert isinstance(OAuthAccount.__table__.c.created_at.type, DateTime)
    finally:
        db.close()


def test_oauth_tokens_are_encrypted_at_rest():
    encrypted_type = EncryptedText()
    stored = encrypted_type.process_bind_param("provider-secret", None)
    assert stored.startswith("gfy1:")
    assert "provider-secret" not in stored
    assert encrypted_type.process_result_value(stored, None) == "provider-secret"


def test_activity_model_includes_created_at_column():
    assert "created_at" in Activity.__table__.columns


def test_production_configuration_rejects_insecure_defaults():
    with pytest.raises(ValidationError, match="Unsafe production configuration"):
        Settings(_env_file=None, ENV="production")
