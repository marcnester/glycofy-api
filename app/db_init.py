# app/db_init.py
from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy import text

from app import models
from app.auth_utils import hash_password
from app.config import settings
from app.db import Base, db_session, engine


def create_all():
    # Import models before create_all to ensure metadata is populated
    _ = models  # noqa: F401
    Base.metadata.create_all(bind=engine)


def ensure_demo_user() -> None:
    from app.models import User  # import after Base/engine is ready

    with db_session() as db:
        email = "demo@glycofy.app"
        user = db.query(User).filter(User.email == email).first()
        if user:
            print(f"✅ User exists: id={user.id}, email={user.email}")
            return

        demo_password = os.getenv("DEMO_PASSWORD", "")
        if len(demo_password) < 12:
            raise RuntimeError("Set DEMO_PASSWORD to at least 12 characters before creating a demo user")
        print("▶ Seeding demo user…")
        user = User(
            email=email.lower(),
            password_hash=hash_password(demo_password),
            sex="male",
            dob=None,
            height_cm=183.0,
            weight_kg=79.0,
            diet_pref="omnivore",
            goal="maintain",
            timezone="America/Los_Angeles",
            created_at=datetime.utcnow(),
        )
        db.add(user)
        # commit via db_session context manager


def print_tables() -> None:
    with engine.connect() as conn:
        try:
            rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")).fetchall()
            names = [r[0] for r in rows]
            print("🗂️  Tables:", names)
        except Exception as e:
            print("(!) Failed listing tables:", e)


if __name__ == "__main__":
    print("DB URL:", settings.DATABASE_URL)
    print("▶ Creating tables (if not exist)…")
    create_all()
    print_tables()
    ensure_demo_user()
    print("✅ Done.")
