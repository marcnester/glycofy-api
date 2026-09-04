from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.auth_utils import get_current_user
from app.config import settings
from app.db import get_db
from app.models import BetaFeedback, ProductEvent, User
from app.observability import request_id

router = APIRouter()

EVENTS = {
    "page_view",
    "onboarding_completed",
    "weekly_plan_started",
    "weekly_plan_completed",
    "grocery_opened",
    "grocery_approved",
    "grocery_handoff_started",
    "request_failed",
    "feedback_opened",
    "feedback_sent",
}


def _browser_family(user_agent: str) -> str:
    value = user_agent.casefold()
    if "edg/" in value:
        return "edge"
    if "firefox/" in value:
        return "firefox"
    if "chrome/" in value or "crios/" in value:
        return "chrome"
    if "safari/" in value:
        return "safari"
    return "other"


def _feature_flags() -> set[str]:
    return set(settings.csv_values(settings.FEATURE_FLAGS))


def _safe_request_id(value: str | None) -> str | None:
    if value and 8 <= len(value) <= 128 and re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        return value
    return None


class ContextIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_path: str = Field(max_length=160)
    viewport: Literal["mobile", "tablet", "desktop"]

    @field_validator("page_path")
    @classmethod
    def safe_page(cls, value: str) -> str:
        path = value.split("?", 1)[0].split("#", 1)[0]
        if not path.startswith("/ui/"):
            raise ValueError("page_path must be a Glycofy UI path")
        return path


class EventIn(ContextIn):
    event_name: str = Field(max_length=48)
    session_id: str = Field(min_length=8, max_length=128)

    @field_validator("event_name")
    @classmethod
    def known_event(cls, value: str) -> str:
        if value not in EVENTS:
            raise ValueError("unknown event")
        return value


class FeedbackIn(ContextIn):
    category: Literal["idea", "issue", "confusing", "praise", "other"]
    rating: int | None = Field(default=None, ge=1, le=5)
    message: str = Field(min_length=3, max_length=1200)
    related_request_id: str | None = Field(default=None, max_length=128)


@router.get("/config")
def beta_config(_user: User = Depends(get_current_user)):
    enabled = _feature_flags()
    return {
        "feedback_enabled": settings.BETA_FEEDBACK_ENABLED and "beta_feedback" in enabled,
        "analytics_enabled": settings.BETA_ANALYTICS_ENABLED and "beta_analytics" in enabled,
        "flags": sorted(enabled),
    }


@router.post("/events", status_code=202)
def record_event(
    payload: EventIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not (settings.BETA_ANALYTICS_ENABLED and "beta_analytics" in _feature_flags()):
        return {"accepted": False}
    session_hash = hmac.new(settings.JWT_SECRET.encode(), payload.session_id.encode(), hashlib.sha256).hexdigest()
    db.add(
        ProductEvent(
            user_id=user.id,
            occurred_at=datetime.utcnow(),
            event_name=payload.event_name,
            page_path=payload.page_path,
            browser_family=_browser_family(request.headers.get("user-agent", "")),
            viewport=payload.viewport,
            session_hash=session_hash,
        )
    )
    db.commit()
    return {"accepted": True}


@router.post("/feedback", status_code=201)
def submit_feedback(
    payload: FeedbackIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not (settings.BETA_FEEDBACK_ENABLED and "beta_feedback" in _feature_flags()):
        raise HTTPException(status_code=404, detail="Not found")
    row = BetaFeedback(
        user_id=user.id,
        category=payload.category,
        rating=payload.rating,
        message=payload.message.strip(),
        page_path=payload.page_path,
        browser_family=_browser_family(request.headers.get("user-agent", "")),
        viewport=payload.viewport,
        request_id=request_id(),
        related_request_id=_safe_request_id(payload.related_request_id),
        status="new",
        created_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"feedback_id": row.id, "status": row.status}
