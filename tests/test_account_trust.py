from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import EnergyTarget, User, UserPreference
from app.rate_limit import AUTH_LIMITER
from app.routers import auth


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
        app.dependency_overrides.clear()
        AUTH_LIMITER.clear()


def _token(message: str) -> str:
    match = re.search(r"[?&]token=([^&\s]+)", message)
    assert match
    return match.group(1)


def test_verification_and_password_reset_are_single_use(client_and_engine, monkeypatch):
    client, engine = client_and_engine
    sent: list[tuple[str, str, str, str]] = []
    monkeypatch.setattr(auth, "account_email_configured", lambda: True)
    monkeypatch.setattr(
        auth, "send_account_email", lambda to, subject, body, html: sent.append((to, subject, body, html))
    )

    signup = client.post("/auth/signup", json={"email": "athlete@example.com", "password": "original-passphrase"})
    assert signup.status_code == 200
    assert signup.json()["verification_sent"] is True
    verification_token = _token(sent[-1][2])
    assert "Verify email address" in sent[-1][3]
    assert f"token={verification_token}" in sent[-1][3]

    verified = client.get(f"/auth/verify-email?token={verification_token}", follow_redirects=False)
    assert verified.status_code == 303
    with Session(engine) as db:
        assert db.query(User).filter_by(email="athlete@example.com").one().email_verified_at is not None

    forgot = client.post("/auth/forgot-password", json={"email": "athlete@example.com"})
    unknown = client.post("/auth/forgot-password", json={"email": "missing@example.com"})
    assert forgot.json() == unknown.json()
    reset_token = _token(sent[-1][2])
    assert "Reset password" in sent[-1][3]
    assert f"token={reset_token}" in sent[-1][3]
    reset = client.post("/auth/reset-password", json={"token": reset_token, "password": "new-secure-passphrase"})
    assert reset.status_code == 200
    assert (
        client.post("/auth/reset-password", json={"token": reset_token, "password": "another-passphrase"}).status_code
        == 400
    )
    assert (
        client.post("/auth/login", json={"email": "athlete@example.com", "password": "original-passphrase"}).status_code
        == 401
    )
    assert (
        client.post(
            "/auth/login", json={"email": "athlete@example.com", "password": "new-secure-passphrase"}
        ).status_code
        == 200
    )


def test_export_includes_preferences_and_deletion_requires_confirmation(client_and_engine):
    client, engine = client_and_engine
    assert (
        client.post(
            "/auth/signup", json={"email": "owner@example.com", "password": "long-secure-passphrase"}
        ).status_code
        == 200
    )
    with Session(engine) as db:
        user = db.query(User).filter_by(email="owner@example.com").one()
        db.add(UserPreference(user_id=user.id, diet_type="omnivore"))
        db.add(EnergyTarget(user_id=user.id, date=__import__("datetime").date.today(), target_kcal=2500))
        db.commit()

    exported = client.get("/users/me/export")
    assert exported.status_code == 200
    assert "attachment" in exported.headers["content-disposition"]
    payload = exported.json()
    assert payload["meal_preferences"][0]["diet_type"] == "omnivore"
    assert payload["energy_targets"][0]["target_kcal"] == 2500
    assert "password_hash" not in payload["account"]

    assert client.request("DELETE", "/users/me", json={"confirmation": "delete"}).status_code == 400
    deleted = client.request("DELETE", "/users/me", json={"confirmation": "DELETE"})
    assert deleted.status_code == 200
    assert client.get("/users/me").status_code == 401
    with Session(engine) as db:
        assert db.query(User).filter_by(email="owner@example.com").first() is None


def test_login_page_exposes_recovery_controls(client_and_engine):
    client, _ = client_and_engine
    page = client.get("/ui/login.html").text
    script = client.get("/ui/login.js").text
    profile = client.get("/ui/profile.html").text
    assert "Forgot password?" in page
    assert "/auth/forgot-password" in script
    assert "/auth/reset-password" in script
    assert 'id="delete_account"' in profile
    assert 'href="/users/me/export"' in profile
