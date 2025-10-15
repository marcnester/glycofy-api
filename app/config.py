from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    ENV: str = "dev"
    PORT: int = 8080
    DATABASE_URL: str = "postgresql+psycopg://postgres:@localhost:5432/glycofy"
    REDIS_URL: str = "redis://localhost:6379/0"
    JWT_SECRET: str = "123yutpwojdmcprld1231"
    STRAVA_CLIENT_ID: str = ""
    STRAVA_CLIENT_SECRET: str = ""
    OAUTH_REDIRECT_URI: str = "http://localhost:8080/oauth/strava/callback"

    class Config:
        env_file = ".env"  # tells FastAPI to load secrets from .env file

settings = Settings()