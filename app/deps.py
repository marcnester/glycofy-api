# app/deps.py
from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.auth_utils import get_current_user as _get_current_user  # source of truth
from app.db import get_db  # re-exported dependency
from app.models import User


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """
    Simple passthrough to auth_utils.get_current_user
    so routers can keep importing from app.deps without tight coupling.
    """
    return _get_current_user(request, db)
