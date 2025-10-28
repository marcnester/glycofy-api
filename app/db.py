from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

# Export Base so models and Alembic can use:
Base = declarative_base()

# DATABASE_URL e.g.:
# - sqlite:///./app.db
# - postgresql+psycopg://user:pass@localhost:5432/dbname
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    DATABASE_URL,
    future=True,
    pool_pre_ping=True,
    connect_args=connect_args,  # type: ignore[arg-type]
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        # Be tolerant during shutdown
        try:
            db.close()
        except Exception:
            pass
