from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.routers.plans import (
    _format_grocery_measurements,
    _grocery_category,
    _grocery_measurement,
    _grocery_name,
    _grocery_unit,
    _package_suggestion,
)


def test_grocery_name_normalizes_aliases_and_whitespace():
    assert _grocery_name("  Mixed   Berries  ") == ("mixed berries", "Mixed berries")
    assert _grocery_name("berries") == ("mixed berries", "Mixed berries")
    assert _grocery_name("eggs") == ("egg", "Eggs")
    assert _grocery_name("fresh spinach") == ("spinach", "Spinach")
    assert _grocery_name("salmon fillet") == ("salmon", "Salmon")


def test_grocery_unit_normalizes_common_plural_units():
    assert _grocery_unit("Tablespoons") == "tbsp"
    assert _grocery_unit("ounces") == "oz"
    assert _grocery_unit("cups") == "cup"


def test_grocery_category_uses_whole_words_and_explicit_metadata():
    assert _grocery_category("Baby spinach") == "Produce"
    assert _grocery_category("Chicken breast") == "Meat & Seafood"
    assert _grocery_category("Veggie mix") == "Other"
    assert _grocery_category("Anything", {"category": "Frozen"}) == "Frozen"
    assert _grocery_category("Almonds", {"category": "Other"}) == "Pantry"


def test_grocery_measurements_parse_fractions_and_convert_compatible_units():
    assert _grocery_measurement(None, "1/2 cup") == ("volume_tbsp", 8)
    assert _grocery_measurement(8, "oz") == ("mass_g", 226.796)
    combined = _format_grocery_measurements("shrimp", {"mass_g": 483.9415}, "US")
    assert combined == {"quantity": 17.1, "unit": "oz", "measurement_summary": None}


def test_piece_weight_bridge_collapses_avocado_grams_and_counts():
    combined = _format_grocery_measurements("avocado", {"mass_g": 150, "count": 1.5}, "US")
    assert combined == {"quantity": 3, "unit": "item", "measurement_summary": None}


def test_package_suggestion_rounds_up_to_purchasable_amounts():
    package = _package_suggestion("salmon", "Meat & Seafood", 18, "oz")
    assert package == {
        "package_size": 16,
        "package_unit": "oz",
        "package_count": 2,
        "purchase_quantity": 32,
        "remainder": 14,
        "source": "estimated",
    }
    assert _package_suggestion("olive oil", "Pantry", 2, "tbsp") is None


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

            grocery = client.get("/v1/plan/grocery-list/week?start=2026-09-03&end=2026-09-04")
            salmon = grocery.json()["items"][0]
            assert salmon["quantity"] == 12
            assert salmon["package"]["package_count"] == 1
            assert salmon["package"]["remainder"] == 4

            preference = client.put(
                "/v1/plan/grocery-list/preferences",
                json={
                    "ingredient_key": "salmon",
                    "preferred_brand": "Harbor Test",
                    "package_quantity": 20,
                    "package_unit": "oz",
                    "in_pantry": False,
                },
            )
            assert preference.status_code == 200
            assert preference.json()["preference"]["preferred_brand"] == "Harbor Test"
            preferred_grocery = client.get("/v1/plan/grocery-list/week?start=2026-09-03&end=2026-09-04")
            preferred_salmon = preferred_grocery.json()["items"][0]
            assert preferred_salmon["package"]["source"] == "preferred"
            assert preferred_salmon["package"]["package_size"] == 20
            assert preferred_salmon["preference"]["preferred_brand"] == "Harbor Test"

            approval = client.post(
                "/v1/plan/grocery-list/approval?start=2026-09-03&end=2026-09-04",
                json={
                    "servings": 2,
                    "items": [
                        {
                            "id": "salmon",
                            "quantity": 24,
                            "unit": "oz",
                            "pantry": False,
                            "preferred_brand": "Harbor Test",
                            "package_quantity": 20,
                            "package_unit": "oz",
                            "purchase_count": 2,
                        }
                    ],
                },
            )
            assert approval.status_code == 200
            assert approval.json()["approval"]["servings"] == 2
            assert approval.json()["approval"]["items"][0]["quantity"] == 24
            assert approval.json()["approval"]["items"][0]["purchase_count"] == 2
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
