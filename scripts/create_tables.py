"""
scripts/create_tables.py
------------------------
Create all tables defined in app.models (idempotent).
Run with:  python -m scripts.create_tables
"""

from __future__ import annotations

# Import models to ensure tables are registered with Base
from app.db import Base, engine


def main() -> int:
    print("🔧 Creating tables if missing...")
    Base.metadata.create_all(bind=engine)
    print("✅ Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
