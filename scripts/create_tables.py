"""
scripts/create_tables.py
------------------------
Create all tables defined in app.models (idempotent).
Run with:  python -m scripts.create_tables
"""

from __future__ import annotations

from app.db import Base, engine
# Import models to ensure tables are registered with Base
import app.models  # noqa: F401


def main() -> int:
    print("🔧 Creating tables if missing...")
    Base.metadata.create_all(bind=engine)
    print("✅ Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())