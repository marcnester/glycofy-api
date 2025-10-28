# api/db.py
"""
Database setup for Glycofy API.
--------------------------------
This module defines the SQLAlchemy Base class (DeclarativeBase) and the
SessionLocal factory for database access across the application.
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


# ---------------------------------------------------------------------
# Base model
# ---------------------------------------------------------------------
class Base(DeclarativeBase):
    """Declarative base class for all SQLAlchemy ORM models."""

    pass


# ---------------------------------------------------------------------
# Database URL and engine configuration
# ---------------------------------------------------------------------


def _database_url() -> str:
    """Resolve the database URL, defaulting to local SQLite."""
    return os.getenv("DATABASE_URL", "sqlite:///./glycofy.db")


DB_URL = _database_url()

# SQLite needs an extra connect arg for multithreading.
connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}

engine = create_engine(DB_URL, connect_args=connect_args, echo=False, future=True)


# ---------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    future=True,
)


# ---------------------------------------------------------------------
# Helper for dependency injection
# ---------------------------------------------------------------------


def get_db():
    """FastAPI dependency that yields a SQLAlchemy session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
