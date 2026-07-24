# app/config.py
from __future__ import annotations

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

    # ─── Units & Defaults ─────────────────────────────────────────────────────
    DEFAULT_UNITS: str = "us"

    # ─── OAuth: Strava ────────────────────────────────────────────────────────
    STRAVA_CLIENT_ID: str | None = None
    STRAVA_CLIENT_SECRET: str | None = None
    STRAVA_REDIRECT_URI: str | None = None

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
    GOOGLE_REDIRECT_URI: str | None = None  # NOTE: matches `.env` key name

    # ─── Auto Sync (optional) ─────────────────────────────────────────────────
    AUTO_SYNC_ENABLED: bool = True
    AUTO_SYNC_INTERVAL_HOURS: int = 24
    AUTO_SYNC_JITTER_SECS: int = 120

    # ─── Paths & Misc (optional) ──────────────────────────────────────────────
    DATA_DIR: str | None = None
    TMP_DIR: str | None = None


# Singleton settings instance
settings = Settings()
