from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


def _database_url() -> str:
    # Separate from app/db.py on purpose; this API sub-package can be a sandbox.
    # Fall back to a local SQLite DB to satisfy mypy/runtime imports for tests/tools.
    return os.getenv("API_DATABASE_URL", "sqlite:///./api.db")


class Base(DeclarativeBase):
    """Declarative base for the `api` package models."""

    pass


DB_URL = _database_url()
connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
engine = create_engine(DB_URL, echo=False, future=True, connect_args=connect_args)

SessionLocal = sessionmaker(
    bind=engine,
    class_=Session,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def get_db() -> Iterator[Session]:
    """FastAPI dependency-style DB session provider."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
