# scripts/reset_demo_password.py
import os

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import User
from app.security import hash_password

DEMO_EMAIL = "demo@glycofy.app"


def main():
    new_password = os.getenv("NEW_DEMO_PASSWORD", "")
    if len(new_password) < 12:
        print("Set NEW_DEMO_PASSWORD to at least 12 characters before running this script")
        return 2
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.email == DEMO_EMAIL).first()
        if not user:
            print(f"User {DEMO_EMAIL} not found")
            return 1
        user.password_hash = hash_password(new_password)
        db.add(user)
        db.commit()
        print(f"✅ Reset password for {DEMO_EMAIL}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
