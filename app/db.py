import os
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

try:
    # Your Settings object (pydantic) lives here
    from app.config import settings  # type: ignore
except Exception:
    settings = None  # fallback to env/DOTENV only


def _database_url() -> str:
    """
    Resolve the database URL with safe fallbacks.
    Priority:
      1) settings.DATABASE_URL (if present)
      2) env var DATABASE_URL
      3) local SQLite file: sqlite:///./glycofy.db
    """
    # Settings may expose DATABASE_URL or database_url; try both.
    url = None
    if settings is not None:
        url = getattr(settings, "DATABASE_URL", None) or getattr(settings, "database_url", None)
        # Some earlier versions used a method:
        if not url and hasattr(settings, "get_database_url"):
            try:
                url = settings.get_database_url()  # type: ignore[attr-defined]
            except Exception:
                url = None

    url = url or os.getenv("DATABASE_URL") or "sqlite:///./glycofy.db"
    return url


DB_URL = _database_url()

# SQLite needs this extra arg; others don’t
connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}

engine = create_engine(DB_URL, future=True, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)

# Base is imported by your models module: from app.db import Base
Base = declarative_base()


def get_db() -> Generator:
    """
    FastAPI dependency to provide a scoped SQLAlchemy Session.
    Usage:
        def endpoint(db: Session = Depends(get_db)): ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()