from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.rate_limit import AUTH_LIMITER
from app.routers import auth


@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    def override_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    AUTH_LIMITER.clear()
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        AUTH_LIMITER.clear()
        app.dependency_overrides.clear()


def test_unknown_account_login_executes_dummy_password_check(client: TestClient, monkeypatch) -> None:
    checked: list[tuple[str, str]] = []

    def fake_verify(password: str, password_hash: str) -> bool:
        checked.append((password, password_hash))
        return False

    monkeypatch.setattr(auth.pwd_context, "verify", fake_verify)
    response = client.post(
        "/auth/login",
        json={"email": "does-not-exist@example.com", "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid_credentials"}
    assert checked == [("wrong-password", auth.DUMMY_PASSWORD_HASH)]


def test_known_and_unknown_accounts_return_same_login_error(client: TestClient) -> None:
    signup = client.post(
        "/auth/signup",
        json={"email": "known@example.com", "password": "valid-password-123"},
    )
    assert signup.status_code == 200
    client.cookies.clear()

    known = client.post(
        "/auth/login",
        json={"email": "known@example.com", "password": "wrong-password"},
    )
    unknown = client.post(
        "/auth/login",
        json={"email": "unknown@example.com", "password": "wrong-password"},
    )

    assert known.status_code == unknown.status_code == 401
    assert known.json() == unknown.json() == {"detail": "invalid_credentials"}


def test_resend_verification_is_rate_limited(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, "AUTH_RATE_LIMIT_PER_15_MINUTES", 1)
    signup = client.post(
        "/auth/signup",
        json={"email": "verify-limit@example.com", "password": "valid-password-123"},
    )
    assert signup.status_code == 200

    assert client.post("/auth/resend-verification").status_code == 200
    blocked = client.post("/auth/resend-verification")

    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0


def test_logout_revokes_other_copies_of_same_session(client: TestClient) -> None:
    signup = client.post(
        "/auth/signup",
        json={"email": "copied-session@example.com", "password": "valid-password-123"},
    )
    assert signup.status_code == 200
    stolen_cookie = client.cookies.get(settings.SESSION_COOKIE_NAME)

    other = TestClient(app)
    other.cookies.set(settings.SESSION_COOKIE_NAME, stolen_cookie)
    assert other.get("/users/me").status_code == 200

    assert client.post("/auth/logout").status_code == 200
    assert other.get("/users/me").status_code == 401
