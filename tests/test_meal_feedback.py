from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import Plan, PlanMeal, User
from app.services.meal_feedback import feedback_context


@pytest.fixture()
def feedback_app():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    def override_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            yield client, engine
    finally:
        app.dependency_overrides.clear()


def _create_meal(client: TestClient, engine, email: str = "athlete@example.com") -> int:
    signup = client.post("/auth/signup", json={"email": email, "password": "a-secure-password-123"})
    assert signup.status_code == 200
    with Session(engine) as db:
        user = db.query(User).filter_by(email=email).one()
        plan = Plan(user_id=user.id, date=date(2026, 9, 2), locked=False, totals={}, source="llm")
        meal = PlanMeal(
            meal_type="dinner",
            title="Lemon Herb Cod",
            kcal=730,
            protein_g=56,
            carbs_g=80,
            fat_g=22,
            order_index=3,
            tags=[],
            meta={},
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        plan.meals.append(meal)
        db.add(plan)
        db.commit()
        db.refresh(meal)
        return meal.id


def test_meal_feedback_round_trip_and_plan_embedding(feedback_app):
    client, engine = feedback_app
    meal_id = _create_meal(client, engine)
    payload = {
        "outcome": "eaten",
        "portion": "too_small",
        "rating": 5,
        "hunger_after": "hungry",
        "energy_after": "great",
        "digestion": "comfortable",
        "practicality": "easy",
        "note": "Great after intervals",
    }

    saved = client.put(f"/v1/feedback/meals/{meal_id}", json=payload)
    assert saved.status_code == 200
    assert saved.json()["meal_title"] == "Lemon Herb Cod"
    assert saved.json()["rating"] == 5

    updated = client.put(f"/v1/feedback/meals/{meal_id}", json={**payload, "portion": "right"})
    assert updated.status_code == 200
    assert updated.json()["portion"] == "right"

    plan = client.get("/v1/plan/2026-09-02")
    assert plan.status_code == 200
    assert plan.json()["meals"][0]["feedback"]["outcome"] == "eaten"

    insights = client.get("/v1/feedback/insights")
    assert insights.status_code == 200
    assert insights.json()["feedback_count"] == 1
    assert insights.json()["favorite_meals"] == ["Lemon Herb Cod"]


def test_feedback_is_private_validated_and_removable(feedback_app):
    client, engine = feedback_app
    meal_id = _create_meal(client, engine)

    invalid = client.put(f"/v1/feedback/meals/{meal_id}", json={"outcome": "eaten", "rating": 6})
    assert invalid.status_code == 422

    client.post("/auth/logout")
    client.post(
        "/auth/signup",
        json={"email": "other@example.com", "password": "another-secure-password-123"},
    )
    forbidden = client.put(f"/v1/feedback/meals/{meal_id}", json={"outcome": "skipped"})
    assert forbidden.status_code == 404

    client.post("/auth/logout")
    client.post("/auth/login", json={"email": "athlete@example.com", "password": "a-secure-password-123"})
    assert client.put(f"/v1/feedback/meals/{meal_id}", json={"outcome": "skipped"}).status_code == 200
    assert client.delete(f"/v1/feedback/meals/{meal_id}").status_code == 204
    assert client.get(f"/v1/feedback/meals/{meal_id}").status_code == 404


def test_feedback_context_summarizes_actionable_signals(feedback_app):
    client, engine = feedback_app
    meal_id = _create_meal(client, engine)
    response = client.put(
        f"/v1/feedback/meals/{meal_id}",
        json={
            "outcome": "skipped",
            "rating": 2,
            "digestion": "poor",
            "practicality": "difficult",
        },
    )
    assert response.status_code == 200

    with Session(engine) as db:
        user = db.query(User).filter_by(email="athlete@example.com").one()
        context = feedback_context(db, user.id)

    assert context["avoid_repeating"] == ["Lemon Herb Cod"]
    assert context["digestion_signals"] == {"poor": 1}
    assert context["practicality_signals"] == {"difficult": 1}
