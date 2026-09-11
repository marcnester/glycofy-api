from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as db_module
from app.db import Base
from app.models import (
    AIOperationMetric,
    NutritionCatalogEntry,
    NutritionValidationJob,
    Plan,
    PlanMeal,
    User,
    WeeklyPlanningJob,
)
from app.routers import llm_recommend, operations
from app.services import usda_nutrition
from app.services.usda_nutrition import FDCMatch, USDAUnavailableError


def _database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _job(job_id: str, *, status: str, attempts: int = 0, completed_at=None, updated_at=None) -> WeeklyPlanningJob:
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
        updated_at=updated_at or datetime.utcnow(),
        completed_at=completed_at,
    )


def test_reconcile_resumes_interrupted_job_and_prunes_old_records(monkeypatch):
    sessions = _database()
    old = datetime.utcnow() - timedelta(days=120)
    with sessions() as db:
        db.add(User(id=1, email="owner@example.com", password_hash="x"))
        db.add_all(
            [
                _job("interrupted", status="running", attempts=1, updated_at=old),
                _job("old", status="completed", completed_at=old),
            ]
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

    assert result == {"recovered": 1, "deferred": 0, "failed": 0, "deleted_jobs": 1, "deleted_metrics": 1}
    assert len(submitted) == 1
    with sessions() as db:
        recovered = db.get(WeeklyPlanningJob, "interrupted")
        assert recovered.status == "queued"
        assert recovered.stage == "recovering"
        assert recovered.worker_id is None


def test_reconcile_defers_fresh_job_during_blue_green_deploy(monkeypatch):
    sessions = _database()
    with sessions() as db:
        db.add(User(id=1, email="owner@example.com", password_hash="x"))
        db.add(_job("still-running", status="running", attempts=1))
        db.commit()

    submitted = []
    timers = []
    monkeypatch.setattr(llm_recommend, "SessionLocal", sessions)
    monkeypatch.setattr(llm_recommend._WEEKLY_JOB_EXECUTOR, "submit", lambda fn, *args: submitted.append((fn, args)))
    monkeypatch.setattr(
        llm_recommend.threading,
        "Timer",
        lambda seconds, fn: timers.append((seconds, fn)) or SimpleNamespace(daemon=False, start=lambda: None),
    )
    monkeypatch.setattr(llm_recommend.settings, "WEEKLY_JOB_RECOVERY_GRACE_SECONDS", 120)

    result = llm_recommend.reconcile_weekly_jobs()

    assert result["recovered"] == 0
    assert result["deferred"] == 1
    assert submitted == []
    assert len(timers) == 1
    with sessions() as db:
        job = db.get(WeeklyPlanningJob, "still-running")
        assert job.status == "running"
        assert job.stage == "running"


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


def test_cancellation_poll_preserves_pending_meal_updates():
    sessions = _database()
    with sessions() as db:
        user = User(id=1, email="owner@example.com", password_hash="x")
        plan = Plan(user_id=1, date=datetime.utcnow().date(), locked=False)
        db.add_all([user, plan])
        db.flush()
        meal = PlanMeal(
            plan_id=plan.id,
            meal_type="snack_2",
            title="Snack_2",
            kcal=0,
            protein_g=0,
            carbs_g=0,
            fat_g=0,
            order_index=3,
        )
        db.add(meal)
        db.add(_job("active-week", status="running", attempts=1))
        db.commit()

        meal.title = "Yogurt Banana Oat Cup"
        meal.kcal = 302
        llm_recommend._WEEKLY_JOB_CONTEXT.job_id = "active-week"
        try:
            llm_recommend._raise_if_weekly_job_cancelled(db)
            db.commit()
        finally:
            llm_recommend._WEEKLY_JOB_CONTEXT.job_id = None

        db.refresh(meal)
        assert meal.title == "Yogurt Banana Oat Cup"
        assert meal.kcal == 302


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


def test_nutrition_source_health_is_privacy_safe(monkeypatch):
    monkeypatch.setattr(operations.settings, "USDA_FDC_REQUIRED", True)
    monkeypatch.setattr(
        operations,
        "lookup_food",
        lambda query: SimpleNamespace(
            fdc_id=171477,
            data_type="SR Legacy",
            nutrients_per_100g={"kcal": 165.0, "protein_g": 31.0, "carbs_g": 0.0, "fat_g": 3.57},
        ),
    )

    result = operations.nutrition_source_health(_admin=SimpleNamespace(email="admin@example.com"))

    assert result == {
        "status": "ok",
        "provider": "USDA FoodData Central",
        "required": True,
        "fdc_id": 171477,
        "data_type": "SR Legacy",
        "nutrients_present": ["carbs_g", "fat_g", "kcal", "protein_g"],
    }


def test_nutrition_validation_summary_exposes_only_aggregate_queue_health():
    sessions = _database()
    now = datetime.utcnow()
    with sessions() as db:
        db.add(
            NutritionCatalogEntry(
                query_key="banana raw",
                fdc_id=173944,
                description="Bananas, raw",
                data_type="Foundation",
                nutrients_per_100g={"kcal": 89, "protein_g": 1.1, "carbs_g": 22.8, "fat_g": 0.3},
                verified_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            NutritionValidationJob(
                query_key="athlete recovery food",
                status="retry",
                attempt_count=2,
                next_attempt_at=now - timedelta(minutes=1),
                created_at=now - timedelta(hours=1),
                updated_at=now,
            )
        )
        db.commit()

        result = operations.nutrition_validation_summary(
            db=db,
            _admin=SimpleNamespace(email="admin@example.com"),
        )

    assert result["catalog_foods"] == 1
    assert result["queue"] == {"retry": 1}
    assert result["due"] == 1
    assert "banana" not in str(result)
    assert "athlete" not in str(result)


def test_nutrition_validation_queue_deduplicates_and_resolves_into_catalog(monkeypatch):
    sessions = _database()
    monkeypatch.setattr(db_module, "SessionLocal", sessions)

    error = USDAUnavailableError("temporarily unavailable")
    usda_nutrition.enqueue_nutrition_validation(" Banana raw ", error)
    usda_nutrition.enqueue_nutrition_validation("banana   raw", error)
    usda_nutrition._store_catalog_match(
        "banana raw",
        FDCMatch(
            fdc_id=173944,
            description="Bananas, raw",
            data_type="Foundation",
            nutrients_per_100g={"kcal": 89, "protein_g": 1.1, "carbs_g": 22.8, "fat_g": 0.3},
        ),
    )

    with sessions() as db:
        assert db.query(NutritionValidationJob).count() == 1
        assert db.query(NutritionValidationJob).one().status == "resolved"
        assert db.query(NutritionCatalogEntry).count() == 1


def test_operator_dashboard_has_latency_failure_cost_and_job_states():
    page = Path("ui/operations.html").read_text(encoding="utf-8")
    script = Path("ui/operations.js").read_text(encoding="utf-8")
    assert "p95 latency" in page
    assert "Failure rate" in page
    assert "Estimated cost" in page
    assert "/v1/operations/ai-summary" in script
    assert "/v1/operations/nutrition-source-health" in script
    assert "USDA available" in script
    assert "/v1/operations/nutrition-validation-summary" in script
    assert "operations.js?v=2026-09-10-nutrition-queue" in page
