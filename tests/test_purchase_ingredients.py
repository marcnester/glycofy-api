from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Plan, PlanItem, PlanMeal, User
from app.routers.plans import _grocery_name, grocery_list_week
from app.services.purchase_ingredients import package_coverage


@pytest.mark.parametrize("name", ["Cod fillet", "raw cod fillet", "cod fillet raw", "frozen cod fillets"])
def test_compatible_cod_names_share_purchase_identity(name):
    assert _grocery_name(name) == ("cod fillet", "Cod fillets")


@pytest.mark.parametrize("name", ["Lettuce", "romaine", "romaine lettuce", "romaine hearts"])
def test_generic_lettuce_uses_romaine_purchase_choice(name):
    assert _grocery_name(name) == ("romaine lettuce", "Romaine lettuce")


@pytest.mark.parametrize(
    "a,b",
    [
        ("cooked cod fillet", "raw cod fillet"),
        ("breaded cod fillets", "cod fillet"),
        ("iceberg lettuce", "romaine lettuce"),
        ("butter lettuce", "lettuce"),
    ],
)
def test_distinct_preparations_and_named_varieties_stay_separate(a, b):
    assert _grocery_name(a)[0] != _grocery_name(b)[0]


@pytest.mark.parametrize(
    "needed,unit,size,count,expected",
    [
        (18, "oz", "6 oz", 3, 3),
        (18, "oz", "12 oz", 2, 2),
        (18, "oz", "32 ounces", 1, 1),
        (480, "g", "1 lb", 2, 2),
        (250, "g", "18 oz", 1, 1),
        (2, "item", "12 ct", 1, 1),
        (18, "oz", "2 x 6 oz", 2, 2),
    ],
)
def test_fixed_retail_package_coverage(needed, unit, size, count, expected):
    result = package_coverage(needed, unit, size, count)
    assert result["status"] == "sufficient"
    assert result["suggested_packages"] == expected
    assert result["selected_quantity"] >= result["required_quantity"]


def test_underbuying_is_visible_without_changing_the_recipe_requirement():
    result = package_coverage(18, "oz", "12 oz", 1)
    assert result["status"] == "short"
    assert result["shortfall"] == 6
    assert result["suggested_packages"] == 2
    assert result["required_quantity"] == 18


@pytest.mark.parametrize(
    "needed,unit,size",
    [
        (250, "g", "1 ct"),
        (2, "item", "18 oz"),
        (18, "oz", "6-8 oz"),
        (18, "oz", "each"),
        (18, "oz", "12 oz / 2 ct"),
        (None, "oz", "12 oz"),
        (float("nan"), "oz", "12 oz"),
        (10, "oz", "0 oz"),
        (10, "oz", "9" * 100 + " oz"),
    ],
)
def test_unknown_or_incompatible_package_measurements_are_not_invented(needed, unit, size):
    assert package_coverage(needed, unit, size)["status"] == "unknown"


def test_merged_shopping_totals_do_not_mutate_recipe_names_weights_or_metadata():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(email="purchase@example.invalid", password_hash="synthetic", units="metric")
        db.add(user)
        db.flush()
        plan = Plan(user_id=user.id, date=date(2026, 11, 9), locked=False)
        plan.meals = [
            PlanMeal(
                meal_type="dinner",
                title="Synthetic shopping fixture",
                items=[
                    PlanItem(name=name, qty=qty, unit="g", meta={"original": True})
                    for name, qty in [
                        ("cod fillet", 300),
                        ("raw cod fillet", 180),
                        ("lettuce", 100),
                        ("romaine lettuce", 150),
                    ]
                ],
            )
        ]
        db.add(plan)
        db.commit()

        def snapshot():
            return [(i.id, i.name, i.qty, i.unit, i.meta) for i in db.query(PlanItem).order_by(PlanItem.id)]

        before = snapshot()
        result = grocery_list_week(start=plan.date, end=plan.date, db=db, user=user)
        assert len(result["items"]) == 2
        items = {item["id"]: item for item in result["items"]}
        assert items["cod fillet"]["quantity"] == 480
        assert items["romaine lettuce"]["quantity"] == 250
        assert items["romaine lettuce"]["shopping_note"]
        db.expire_all()
        assert snapshot() == before
