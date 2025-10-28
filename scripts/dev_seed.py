"""
Seed script for Glycofy (creates tables and a demo user).
Run with:  python -m scripts.dev_seed
"""

from __future__ import annotations
import sys

from passlib.hash import bcrypt_sha256
from sqlalchemy.exc import IntegrityError

from app.db import Base, engine, SessionLocal
from app.models import User  # assumes you already have a User model with these fields


DEMO_EMAIL = "demo@glycofy.app"
DEMO_PASSWORD = "Demo1234!"  # keep <=72 bytes; bcrypt has a 72-byte limit


def main() -> int:
    # 1) ensure tables exist
    Base.metadata.create_all(bind=engine)

    # 2) insert demo user if not exists
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == DEMO_EMAIL).first()
        if user:
            print(f"ℹ️  User already exists: {DEMO_EMAIL} (id={user.id})")
            return 0

        pwd_hash = bcrypt_sha256.hash(DEMO_PASSWORD)
        user = User(
            email=DEMO_EMAIL,
            password_hash=pwd_hash,
            sex="male",
            height_cm=180.0,
            weight_kg=80.0,
            diet_pref="pescatarian",
            goal="maintain",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        print(f"✅ Created user: {DEMO_EMAIL} (id={user.id})")
        print("   You can login with:")
        print(f"   email:    {DEMO_EMAIL}")
        print(f"   password: {DEMO_PASSWORD}")
        return 0
    except IntegrityError:
        db.rollback()
        print(f"ℹ️  User already exists: {DEMO_EMAIL}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())