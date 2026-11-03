from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.config import Settings


def hardened_settings(**changes):
    values = dict(
        ENV="production",
        DEBUG=False,
        JWT_SECRET="synthetic-jwt-key-for-config-tests-only-2026",
        COOKIE_SECURE=True,
        PUBLIC_BASE_URL="https://staging.glycofy.ai",
        DATABASE_URL="postgresql+psycopg://unused:unused@localhost/unused",
        ENABLE_DEV_ROUTES=False,
        TRUSTED_EDGE_PROVIDER="cloudflare",
        EDGE_ORIGIN_SECRET="synthetic-edge-key-for-config-tests-only-2026",
        PASSWORD_BREACH_CHECK_ENABLED=True,
        REQUIRE_STATEFUL_SESSIONS=True,
        OAUTH_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(),
        WEBAUTHN_RP_ID="staging.glycofy.ai",
        WEBAUTHN_EXPECTED_ORIGIN="https://staging.glycofy.ai",
        SECURITY_ALERT_EMAIL_ENABLED=False,
        BETA_FEEDBACK_EMAIL_ENABLED=False,
    )
    values.update(changes)
    return Settings(_env_file=None, **values)


def test_hardened_staging_uses_production_security_with_its_own_passkey_origin():
    config = hardened_settings()
    assert config.is_production
    assert config.session_cookie_name.startswith("__Host-")


def test_existing_production_origin_stays_valid():
    config = hardened_settings(
        PUBLIC_BASE_URL="https://app.glycofy.ai",
        WEBAUTHN_RP_ID="app.glycofy.ai",
        WEBAUTHN_EXPECTED_ORIGIN="https://app.glycofy.ai",
    )
    assert config.is_production


@pytest.mark.parametrize(
    "changes",
    [
        {"WEBAUTHN_RP_ID": "glycofy.ai"},
        {"WEBAUTHN_EXPECTED_ORIGIN": "https://app.glycofy.ai"},
        {"PUBLIC_BASE_URL": "https://user:password@staging.glycofy.ai"},
        {"PUBLIC_BASE_URL": "https://staging.glycofy.ai/login"},
        {"PUBLIC_BASE_URL": "https://staging.glycofy.ai?redirect=evil"},
        {"PUBLIC_BASE_URL": "http://staging.glycofy.ai"},
        {"EDGE_ORIGIN_SECRET": ""},
        {"TRUSTED_EDGE_PROVIDER": ""},
        {"DATABASE_URL": "sqlite:///local.db"},
        {"COOKIE_SECURE": False},
    ],
)
def test_staging_hostname_does_not_bypass_production_guards(changes):
    with pytest.raises(ValidationError):
        hardened_settings(**changes)
