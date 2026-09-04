from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import AIOperationMetric, User, WeeklyPlanningJob
from app.routers import llm_recommend, operations


def _database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _job(job_id: str, *, status: str, attempts: int = 0, completed_at=None) -> WeeklyPlanningJob:
    return WeeklyPlanningJob(
        id=job_id,
        user_id=1,
        status=status,
        stage=status,
        message=status,
        completed_days=0,
        total_days=1,
        payload={"days": [{"date": "2026-09-03", "meals": []}]},
        cancel_requested=False,
        attempt_count=attempts,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        completed_at=completed_at,
    )


def test_reconcile_resumes_interrupted_job_and_prunes_old_records(monkeypatch):
    sessions = _database()
    old = datetime.utcnow() - timedelta(days=120)
    with sessions() as db:
        db.add(User(id=1, email="owner@example.com", password_hash="x"))
        db.add_all(
            [_job("interrupted", status="running", attempts=1), _job("old", status="completed", completed_at=old)]
        )
        db.add(
            AIOperationMetric(
                occurred_at=old,
                operation="weekly_plan",
                provider="openai",
                status="success",
                estimated_cost_usd=0.01,
            )
        )
        db.commit()

    submitted = []
    monkeypatch.setattr(llm_recommend, "SessionLocal", sessions)
    monkeypatch.setattr(llm_recommend._WEEKLY_JOB_EXECUTOR, "submit", lambda fn, *args: submitted.append((fn, args)))
    monkeypatch.setattr(llm_recommend.settings, "WEEKLY_JOB_RETENTION_DAYS", 30)
    monkeypatch.setattr(llm_recommend.settings, "AI_METRIC_RETENTION_DAYS", 90)

    result = llm_recommend.reconcile_weekly_jobs()

    assert result == {"recovered": 1, "failed": 0, "deleted_jobs": 1, "deleted_metrics": 1}
    assert len(submitted) == 1
    with sessions() as db:
        recovered = db.get(WeeklyPlanningJob, "interrupted")
        assert recovered.status == "queued"
        assert recovered.stage == "recovering"
        assert recovered.worker_id is None


def test_reconcile_fails_job_after_bounded_recovery_attempts(monkeypatch):
    sessions = _database()
    with sessions() as db:
        db.add(User(id=1, email="owner@example.com", password_hash="x"))
        db.add(_job("exhausted", status="running", attempts=3))
        db.commit()
    monkeypatch.setattr(llm_recommend, "SessionLocal", sessions)
    monkeypatch.setattr(llm_recommend.settings, "WEEKLY_JOB_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(llm_recommend._WEEKLY_JOB_EXECUTOR, "submit", lambda *_args: pytest.fail("must not submit"))

    result = llm_recommend.reconcile_weekly_jobs()

    assert result["failed"] == 1
    with sessions() as db:
        job = db.get(WeeklyPlanningJob, "exhausted")
        assert job.status == "failed"
        assert job.error_code == "recovery_attempts_exhausted"
        assert len(job.error_reference) == 32


def test_ai_summary_is_aggregate_only(monkeypatch):
    sessions = _database()
    now = datetime.utcnow()
    with sessions() as db:
        db.add(User(id=1, email="admin@example.com", password_hash="x"))
        db.add_all(
            [
                AIOperationMetric(
                    occurred_at=now,
                    operation="weekly_plan",
                    provider="openai",
                    status="success",
                    latency_ms=100,
                    input_tokens=10,
                    output_tokens=20,
                    estimated_cost_usd=0.01,
                ),
                AIOperationMetric(
                    occurred_at=now,
                    operation="weekly_plan",
                    provider="openai",
                    status="failed",
                    latency_ms=300,
                    input_tokens=5,
                    output_tokens=0,
                    estimated_cost_usd=0.002,
                ),
            ]
        )
        db.commit()
        summary = operations.ai_summary(hours=24, db=db, _admin=SimpleNamespace(email="admin@example.com"))

    assert summary["requests"] == 2
    assert summary["failure_rate"] == 0.5
    assert summary["latency_ms"] == {"p50": 100, "p95": 300}
    assert summary["tokens"] == {"input": 15, "output": 20}
    assert not ({"users", "prompts", "meals", "health"} & set(summary))


def test_operations_endpoint_is_hidden_from_non_admin(monkeypatch):
    monkeypatch.setattr(operations.settings, "ADMIN_EMAILS", "admin@example.com")
    assert operations._require_admin(SimpleNamespace(email="ADMIN@example.com")).email == "ADMIN@example.com"
    with pytest.raises(HTTPException) as exc:
        operations._require_admin(SimpleNamespace(email="member@example.com"))
    assert exc.value.status_code == 404


def test_operator_dashboard_has_latency_failure_cost_and_job_states():
    page = Path("ui/operations.html").read_text(encoding="utf-8")
    script = Path("ui/operations.js").read_text(encoding="utf-8")
    assert "p95 latency" in page
    assert "Failure rate" in page
    assert "Estimated cost" in page
    assert "/v1/operations/ai-summary" in script
