# app/config.py

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # API / Server
    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8090

    # Database
    DATABASE_URL: str = "sqlite:///./glycofy.db"

    # Auth / JWT
    JWT_SECRET: str = "dev_fallback_secret_change_me"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # Strava OAuth
    STRAVA_CLIENT_ID: str | None = None
    STRAVA_CLIENT_SECRET: str | None = None
    STRAVA_REDIRECT_URI: str | None = None

    # Google OAuth
    GOOGLE_CLIENT_ID: str | None = None
    GOOGLE_CLIENT_SECRET: str | None = None
    GOOGLE_REDIRECT_URL: str | None = None  # note: we use *_URL consistently


settings = Settings()
