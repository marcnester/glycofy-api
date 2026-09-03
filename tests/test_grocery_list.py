from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.routers.plans import _grocery_category, _grocery_name, _grocery_unit


def test_grocery_name_normalizes_aliases_and_whitespace():
    assert _grocery_name("  Mixed   Berries  ") == ("mixed berries", "Mixed berries")
    assert _grocery_name("berries") == ("mixed berries", "Mixed berries")
    assert _grocery_name("eggs") == ("egg", "Eggs")


def test_grocery_unit_normalizes_common_plural_units():
    assert _grocery_unit("Tablespoons") == "tbsp"
    assert _grocery_unit("ounces") == "oz"
    assert _grocery_unit("cups") == "cup"


def test_grocery_category_uses_whole_words_and_explicit_metadata():
    assert _grocery_category("Baby spinach") == "Produce"
    assert _grocery_category("Chicken breast") == "Meat & Seafood"
    assert _grocery_category("Veggie mix") == "Other"
    assert _grocery_category("Anything", {"category": "Frozen"}) == "Frozen"


def test_weekly_grocery_approval_is_persisted_and_detects_plan_changes():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    def override_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    meal = {
        "meal_type": "dinner",
        "title": "Salmon bowl",
        "ingredients": [{"name": "salmon", "qty": 6, "unit": "oz"}],
    }
    try:
        with TestClient(app) as client:
            client.post("/auth/signup", json={"email": "shopper@example.com", "password": "a-secure-password-123"})
            for day in ("2026-09-03", "2026-09-04"):
                assert client.post(f"/v1/plan/{day}", json={"meals": [meal], "source": "llm"}).status_code == 200

            approval = client.post(
                "/v1/plan/grocery-list/approval?start=2026-09-03&end=2026-09-04",
                json={
                    "servings": 2,
                    "items": [{"id": "salmon:oz", "quantity": 24, "unit": "oz", "pantry": False}],
                },
            )
            assert approval.status_code == 200
            assert approval.json()["approval"]["servings"] == 2
            assert approval.json()["approval"]["items"][0]["quantity"] == 24
            assert approval.json()["approval"]["stale"] is False

            status = client.get("/v1/plan/grocery-list/approval?start=2026-09-03&end=2026-09-04")
            assert status.json()["approval"]["stale"] is False

            changed_meal = {**meal, "title": "Updated salmon bowl"}
            client.post("/v1/plan/2026-09-04", json={"meals": [changed_meal], "source": "llm"})
            stale = client.get("/v1/plan/grocery-list/approval?start=2026-09-03&end=2026-09-04")
            assert stale.json()["approval"]["stale"] is True
    finally:
        app.dependency_overrides.clear()


def test_grocery_approval_requires_every_selected_day_to_be_planned():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    def override_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            client.post("/auth/signup", json={"email": "incomplete@example.com", "password": "a-secure-password-123"})
            client.post(
                "/v1/plan/2026-09-03",
                json={"meals": [{"meal_type": "dinner", "title": "Dinner"}], "source": "llm"},
            )
            response = client.post(
                "/v1/plan/grocery-list/approval?start=2026-09-03&end=2026-09-04",
                json={"servings": 1, "items": []},
            )
            assert response.status_code == 400
            assert "Every selected day" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()
