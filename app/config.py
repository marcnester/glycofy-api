# app/config.py
from __future__ import annotations

import hashlib

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Centralized environment configuration for Glycofy.

    - Reads from `.env` in repo root.
    - Case-insensitive keys.
    - Ignores unknown/extra keys so older envs won't crash.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ─── Application ──────────────────────────────────────────────────────────
    APP_NAME: str = "Glycofy API"
    ENV: str = "development"  # development | staging | production
    DEBUG: bool = True
    LOG_LEVEL: str = "info"

    # ─── API / Server ─────────────────────────────────────────────────────────
    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8090
    PUBLIC_BASE_URL: str | None = "http://127.0.0.1:8090"
    SERVE_UI_FROM_API: bool = True
    ALLOWED_ORIGINS: str = "http://127.0.0.1:8090,http://localhost:8090"
    ALLOWED_HOSTS: str = "127.0.0.1,localhost,testserver"
    ENABLE_DEV_ROUTES: bool = False
    MAX_REQUEST_BODY_BYTES: int = 1_048_576
    AUTH_RATE_LIMIT_PER_15_MINUTES: int = 20
    OAUTH_RATE_LIMIT_PER_15_MINUTES: int = 30
    SECURITY_AUDIT_RETENTION_DAYS: int = 365
    SECURITY_ALERT_EMAIL_TO: str = ""
    SECURITY_ALERT_EMAIL_ENABLED: bool = False
    SECURITY_ALERT_EMAIL_COOLDOWN_SECONDS: int = 900
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_FROM_EMAIL: str | None = None
    SMTP_USE_TLS: bool = True

    # ─── Database ─────────────────────────────────────────────────────────────
    # Example: sqlite:////absolute/path/to/glycofy.db
    DATABASE_URL: str = "sqlite:///./glycofy.db"

    # ─── Auth / JWT ───────────────────────────────────────────────────────────
    JWT_SECRET: str = "dev_fallback_secret_change_me"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    JWT_ISS: str | None = "glyco.local"
    JWT_AUD: str | None = "glyco.web"
    ID_COOKIE_NAME: str = "id_token"
    SESSION_COOKIE_NAME: str = "access_token"
    COOKIE_SECURE: bool = False
    OAUTH_STATE_TTL_SECONDS: int = 600
    OAUTH_TOKEN_ENCRYPTION_KEY: str | None = None

    # ─── Units & Defaults ─────────────────────────────────────────────────────
    DEFAULT_UNITS: str = "us"

    # ─── OAuth: Strava ────────────────────────────────────────────────────────
    STRAVA_CLIENT_ID: str | None = None
    STRAVA_CLIENT_SECRET: str | None = None
    STRAVA_REDIRECT_URI: str | None = None
    STRAVA_WEBHOOK_VERIFY_TOKEN: str | None = None
    STRAVA_WEBHOOK_SUBSCRIPTION_ID: int | None = None

    # ─── Grocery checkout (optional) ─────────────────────────────────────────
    INSTACART_API_KEY: str | None = None
    INSTACART_API_BASE: str = "https://connect.instacart.com"
    INSTACART_LINK_EXPIRES_DAYS: int = 30

    # TrainingPeaks is approved-partner only. These remain unset until Glycofy
    # receives its assigned OAuth endpoints and credentials.
    TRAININGPEAKS_CLIENT_ID: str | None = None
    TRAININGPEAKS_CLIENT_SECRET: str | None = None
    TRAININGPEAKS_REDIRECT_URI: str | None = None

    def trainingpeaks_ready(self) -> bool:
        return bool(
            self.TRAININGPEAKS_CLIENT_ID and self.TRAININGPEAKS_CLIENT_SECRET and self.TRAININGPEAKS_REDIRECT_URI
        )

    def strava_ready(self) -> bool:
        """Check whether Strava OAuth is fully configured."""
        return bool(self.STRAVA_CLIENT_ID and self.STRAVA_CLIENT_SECRET and self.STRAVA_REDIRECT_URI)

    def strava_auth_url(self, scope: str = "activity:read_all") -> str | None:
        """Constructs the Strava authorization URL, if configured."""
        if not self.strava_ready():
            return None
        return (
            "https://www.strava.com/oauth/authorize"
            f"?client_id={self.STRAVA_CLIENT_ID}"
            f"&response_type=code"
            f"&redirect_uri={self.STRAVA_REDIRECT_URI}"
            f"&approval_prompt=auto"
            f"&scope={scope}"
        )

    # ─── OAuth: Google ────────────────────────────────────────────────────────
    GOOGLE_CLIENT_ID: str | None = None
    GOOGLE_CLIENT_SECRET: str | None = None
    GOOGLE_REDIRECT_URI: str | None = None
    GOOGLE_REDIRECT_URL: str | None = None  # legacy environment name

    # ─── Auto Sync (optional) ─────────────────────────────────────────────────
    AUTO_SYNC_ENABLED: bool = True
    AUTO_SYNC_INTERVAL_HOURS: int = 24
    AUTO_SYNC_JITTER_SECS: int = 120

    # ─── Paths & Misc (optional) ──────────────────────────────────────────────
    DATA_DIR: str | None = None
    TMP_DIR: str | None = None

    @property
    def is_production(self) -> bool:
        return self.ENV.strip().lower() == "production"

    def csv_values(self, value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]

    @model_validator(mode="after")
    def reject_unsafe_production_defaults(self):
        if not self.is_production:
            # Avoid weak HMAC keys in zero-config local development without
            # exposing or rewriting the developer's .env value.
            if len(self.JWT_SECRET) < 32:
                self.JWT_SECRET = hashlib.sha256(f"glycofy-dev:{self.JWT_SECRET}".encode()).hexdigest()
            return self
        errors: list[str] = []
        if self.DEBUG:
            errors.append("DEBUG must be false")
        if self.JWT_SECRET in {"", "dev_fallback_secret_change_me", "dev-insecure-secret-change-me"}:
            errors.append("JWT_SECRET must be replaced")
        if len(self.JWT_SECRET) < 32:
            errors.append("JWT_SECRET must contain at least 32 characters")
        if not self.COOKIE_SECURE:
            errors.append("COOKIE_SECURE must be true")
        if not (self.PUBLIC_BASE_URL or "").startswith("https://"):
            errors.append("PUBLIC_BASE_URL must use https")
        if self.DATABASE_URL.startswith("sqlite"):
            errors.append("DATABASE_URL must use a production database")
        if self.ENABLE_DEV_ROUTES:
            errors.append("ENABLE_DEV_ROUTES must be false")
        if self.AUTH_RATE_LIMIT_PER_15_MINUTES < 1:
            errors.append("AUTH_RATE_LIMIT_PER_15_MINUTES must be positive")
        if self.OAUTH_RATE_LIMIT_PER_15_MINUTES < 1:
            errors.append("OAUTH_RATE_LIMIT_PER_15_MINUTES must be positive")
        if not self.OAUTH_TOKEN_ENCRYPTION_KEY:
            errors.append("OAUTH_TOKEN_ENCRYPTION_KEY must be configured")
        if self.strava_ready() and len(self.STRAVA_WEBHOOK_VERIFY_TOKEN or "") < 32:
            errors.append("STRAVA_WEBHOOK_VERIFY_TOKEN must contain at least 32 characters")
        if self.INSTACART_API_KEY and self.INSTACART_API_BASE.rstrip("/") != "https://connect.instacart.com":
            errors.append("INSTACART_API_BASE must use the production Instacart endpoint")
        if self.SECURITY_ALERT_EMAIL_ENABLED:
            if not self.SMTP_HOST:
                errors.append("SMTP_HOST is required when alert email is enabled")
            if not self.SMTP_FROM_EMAIL:
                errors.append("SMTP_FROM_EMAIL is required when alert email is enabled")
            if not self.SECURITY_ALERT_EMAIL_TO:
                errors.append("SECURITY_ALERT_EMAIL_TO is required when alert email is enabled")
        if errors:
            raise ValueError("Unsafe production configuration: " + "; ".join(errors))
        return self


# Singleton settings instance
settings = Settings()
