# scripts/reset_demo_password.py
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import User
from app.security import hash_password

DEMO_EMAIL = "demo@glycofy.app"
NEW_PWD = "Demo1234!"


def main():
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.email == DEMO_EMAIL).first()
        if not user:
            print(f"User {DEMO_EMAIL} not found")
            return 1
        user.password_hash = hash_password(NEW_PWD)
        db.add(user)
        db.commit()
        print(f"✅ Reset password for {DEMO_EMAIL} to {NEW_PWD}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
