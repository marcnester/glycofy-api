from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.password_security import PasswordPolicyError, PasswordScreenUnavailable, validate_new_password
from app.rate_limit import AUTH_LIMITER, client_address
from app.routers import oauth_google


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


def test_browser_hardening_headers_and_authenticated_no_store(client: TestClient):
    public = client.get("/health")
    assert "base-uri 'none'" in public.headers["content-security-policy"]

    signup = client.post(
        "/auth/signup",
        json={"email": "headers@example.com", "password": "high-entropy-correct-staple-4817"},
    )
    assert signup.status_code == 200
    authenticated = client.get("/users/me")
    assert authenticated.headers["cache-control"] == "no-store"

    logout = client.post("/auth/logout")
    assert logout.headers["clear-site-data"] == '"cache", "storage"'


def test_password_policy_rejects_common_contextual_and_breached_values(monkeypatch):
    with pytest.raises(PasswordPolicyError):
        validate_new_password("passwordpassword", email="owner@example.com")
    with pytest.raises(PasswordPolicyError):
        validate_new_password("glycofy-athlete-2026", email="owner@example.com")

    monkeypatch.setattr(settings, "PASSWORD_BREACH_CHECK_ENABLED", True)
    monkeypatch.setattr("app.password_security._is_breached", lambda password: True)
    with pytest.raises(PasswordPolicyError, match="data breach"):
        validate_new_password("high-entropy-correct-staple-4817", email="owner@example.com")


def test_password_breach_screen_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "PASSWORD_BREACH_CHECK_ENABLED", True)

    def unavailable(password):
        raise httpx.TimeoutException("provider timeout")

    monkeypatch.setattr("app.password_security._is_breached", unavailable)
    with pytest.raises(PasswordScreenUnavailable, match="temporarily unavailable"):
        validate_new_password("high-entropy-correct-staple-4817", email="owner@example.com")


def test_change_password_requires_current_password_and_revokes_session(client: TestClient):
    old_password = "high-entropy-original-staple-4817"
    new_password = "high-entropy-replacement-staple-9254"
    assert (
        client.post("/auth/signup", json={"email": "change@example.com", "password": old_password}).status_code == 200
    )

    rejected = client.post(
        "/auth/change-password",
        json={"current_password": "incorrect-password", "new_password": new_password},
    )
    assert rejected.status_code == 400

    changed = client.post(
        "/auth/change-password",
        json={"current_password": old_password, "new_password": new_password},
    )
    assert changed.status_code == 200
    assert changed.headers["clear-site-data"] == '"cache", "storage"'
    assert client.get("/users/me").status_code == 401
    assert client.post("/auth/login", json={"email": "change@example.com", "password": old_password}).status_code == 401
    assert client.post("/auth/login", json={"email": "change@example.com", "password": new_password}).status_code == 200


def test_production_client_address_uses_only_valid_cloudflare_assertion(monkeypatch):
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(settings, "TRUSTED_EDGE_PROVIDER", "cloudflare")
    request = Request(
        {
            "type": "http",
            "headers": [
                (b"cf-connecting-ip", b"203.0.113.9"),
                (b"x-forwarded-for", b"198.51.100.77"),
            ],
            "client": ("10.0.0.5", 1234),
        }
    )
    assert client_address(request) == "203.0.113.9"

    malformed = Request(
        {
            "type": "http",
            "headers": [(b"cf-connecting-ip", b"not-an-ip"), (b"x-forwarded-for", b"198.51.100.77")],
            "client": ("10.0.0.5", 1234),
        }
    )
    assert client_address(malformed) == "unverified-edge"


def test_production_cookie_name_and_server_proxy_boundary(monkeypatch):
    monkeypatch.setattr(settings, "ENV", "production")
    monkeypatch.setattr(settings, "SESSION_COOKIE_NAME", "access_token")
    assert settings.session_cookie_name == "__Host-access_token"

    dockerfile = Path("Dockerfile").read_text()
    assert "--no-proxy-headers" in dockerfile
    assert "--forwarded-allow-ips" not in dockerfile


def test_google_id_token_requires_signature_audience_issuer_and_nonce(monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": "test-key", "alg": "RS256", "use": "sig"})

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"keys": [public_jwk]}

    class FakeClient:
        async def get(self, url):
            assert url == oauth_google.GOOGLE_JWKS_URL
            return FakeResponse()

    monkeypatch.setattr(oauth_google, "GOOGLE_CLIENT_ID", "client-id")
    claims = {
        "iss": "https://accounts.google.com",
        "aud": "client-id",
        "sub": "google-subject",
        "email": "athlete@example.com",
        "email_verified": True,
        "nonce": "expected-nonce",
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
    }
    token = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})
    validated = asyncio.run(oauth_google._validate_google_id_token(token, "expected-nonce", FakeClient()))
    assert validated["sub"] == "google-subject"

    with pytest.raises(HTTPException, match="Google identity validation failed"):
        asyncio.run(oauth_google._validate_google_id_token(token, "wrong-nonce", FakeClient()))
