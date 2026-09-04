from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.models import BetaFeedback, ProductEvent, User, WeeklyPlanningJob


@pytest.fixture()
def beta_app(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    def override_db():
        with Session(engine) as db:
            yield db

    monkeypatch.setattr(settings, "FEATURE_FLAGS", "beta_feedback,beta_analytics")
    monkeypatch.setattr(settings, "BETA_FEEDBACK_ENABLED", True)
    monkeypatch.setattr(settings, "BETA_ANALYTICS_ENABLED", True)
    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            assert (
                client.post(
                    "/auth/signup", json={"email": "beta@example.com", "password": "a-secure-password-123"}
                ).status_code
                == 200
            )
            yield client, engine
    finally:
        app.dependency_overrides.clear()


def test_feedback_attaches_safe_context_without_health_payload(beta_app):
    client, engine = beta_app
    response = client.post(
        "/v1/beta/feedback",
        headers={"User-Agent": "Mozilla/5.0 Chrome/140.0 Safari/537.36", "X-Request-ID": "feedback-request-123"},
        json={
            "category": "confusing",
            "rating": 3,
            "message": "I could not tell whether the plan had finished.",
            "page_path": "/ui/plan.html?date=private",
            "viewport": "desktop",
            "related_request_id": "prior-request-456",
        },
    )
    assert response.status_code == 201
    with Session(engine) as db:
        row = db.query(BetaFeedback).one()
        assert row.page_path == "/ui/plan.html"
        assert row.browser_family == "chrome"
        assert row.request_id == "feedback-request-123"
        assert row.related_request_id == "prior-request-456"
        assert not hasattr(row, "health_data")

    rejected = client.post(
        "/v1/beta/feedback",
        json={
            "category": "issue",
            "message": "problem",
            "page_path": "https://attacker.example/path",
            "viewport": "desktop",
            "health": {"weight": 80},
        },
    )
    assert rejected.status_code == 422


def test_analytics_accepts_only_allowlisted_events_and_hashes_session(beta_app):
    client, engine = beta_app
    payload = {
        "event_name": "weekly_plan_completed",
        "page_path": "/ui/plan.html",
        "viewport": "mobile",
        "session_id": "browser-session-123",
    }
    assert client.post("/v1/beta/events", json=payload).status_code == 202
    assert client.post("/v1/beta/events", json={**payload, "event_name": "meal_health_data"}).status_code == 422
    with Session(engine) as db:
        row = db.query(ProductEvent).one()
        assert row.event_name == "weekly_plan_completed"
        assert row.session_hash != payload["session_id"]
        assert len(row.session_hash) == 64


def test_admin_beta_views_are_aggregate_and_failed_jobs_are_sanitized(beta_app, monkeypatch):
    client, engine = beta_app
    monkeypatch.setattr(settings, "ADMIN_EMAILS", "beta@example.com")
    with Session(engine) as db:
        user = db.query(User).filter_by(email="beta@example.com").one()
        db.add(
            WeeklyPlanningJob(
                id="failed-job",
                user_id=user.id,
                status="failed",
                stage="failed",
                message="failed",
                completed_days=0,
                total_days=7,
                payload={"private": "meal and health payload"},
                error="private provider response",
                error_code="TimeoutError",
                error_reference="reference1234",
                attempt_count=2,
                cancel_requested=False,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
                completed_at=datetime.utcnow(),
            )
        )
        db.commit()
    summary = client.get("/v1/operations/beta-summary")
    jobs = client.get("/v1/operations/failed-jobs")
    assert summary.status_code == 200
    assert summary.json()["active_users"] == 0
    assert jobs.status_code == 200
    assert jobs.json()[0]["error_reference"] == "reference1234"
    assert "private" not in str(jobs.json()).lower()


def test_feedback_control_is_present_on_every_authenticated_primary_page():
    for name in ("index.html", "plan.html", "profile.html", "activities.html", "plan-week.html", "grocery.html"):
        assert "/ui/beta.js" in Path(f"ui/{name}").read_text(encoding="utf-8")
    script = Path("ui/beta.js").read_text(encoding="utf-8")
    assert "We never attach meals, health information, or activity details" in script
    assert "beta-feedback-button" in script
