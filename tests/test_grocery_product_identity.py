from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Plan, PlanItem, PlanMeal, User
from app.routers import plans


@pytest.mark.parametrize(
    "alias,canonical",
    [
        ("Whole egg", "egg"),
        ("eggs whole raw", "egg"),
        ("russet potato with skin", "russet potatoes raw with skin"),
        ("mozzarella cheese whole milk", "whole milk mozzarella"),
        ("dry couscous", "couscous dry"),
        ("canned tuna in water, drained", "canned tuna in water"),
        ("Greek yogurt plain nonfat", "plain nonfat greek yogurt"),
        ("Egg whole", "egg"),
        ("eggs", "egg"),
        ("raw cod fillet", "cod fillet raw"),
        ("corn tortillas", "corn tortilla"),
        ("rice cakes", "rice cake"),
        ("ginger root", "ginger"),
        ("skyr plain nonfat", "plain nonfat skyr"),
        ("plain greek yogurt 2% milkfat", "greek yogurt plain 2 milkfat"),
    ],
)
def test_observed_grocery_duplicates_share_product_identity(alias, canonical):
    assert plans._grocery_name(alias)[0] == canonical


def test_nutritionally_distinct_products_are_not_blindly_merged():
    for a, b in [
        ("brown rice dry", "cooked brown rice"),
        ("dry couscous", "cooked couscous"),
        ("canned tuna in water", "canned tuna in oil"),
        ("plain nonfat Greek yogurt", "Greek yogurt plain 2% milkfat"),
        ("almond butter", "peanut butter"),
        ("nonfat Greek yogurt", "plain nonfat Greek yogurt"),
        ("russet potatoes", "russet potatoes raw with skin"),
        ("cod fillet", "raw cod fillet"),
        ("corn kernels", "corn"),
    ]:
        assert plans._grocery_name(a)[0] != plans._grocery_name(b)[0]


@pytest.mark.parametrize(
    "name,category",
    [
        ("black pepper", "Pantry"),
        ("chickpeas canned drained", "Plant Proteins"),
        ("firm tofu prepared with calcium sulfate", "Plant Proteins"),
        ("russet potatoes raw with skin", "Produce"),
        ("corn tortillas", "Grains & Bakery"),
        ("green beans", "Produce"),
        ("potato", "Produce"),
        ("blueberries", "Produce"),
        ("plain nonfat skyr", "Dairy & Eggs"),
        ("trout fillet raw", "Meat & Seafood"),
    ],
)
def test_observed_misclassifications_are_corrected(name, category):
    assert plans._grocery_category(name) == category


def test_unknown_packages_are_not_invented_and_eggs_are_counted():
    for name in ["apple", "baking powder", "salmon", "greek yogurt"]:
        assert plans._package_suggestion(name, plans._grocery_category(name), 2, "oz") is None
    assert plans._format_grocery_measurements("egg", {"mass_g": 250, "count": 1}, "US")["quantity"] == 6


def test_grocery_aggregation_preserves_legacy_preferences_after_alias_merge():
    from app.models import GroceryPreference

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(email="aliases@example.com", password_hash="test")
        db.add(user)
        db.flush()
        plan = Plan(user_id=user.id, date=date(2026, 11, 2), locked=False)
        plan.meals = [
            PlanMeal(
                meal_type="breakfast",
                title="Eggs",
                items=[
                    PlanItem(name="whole egg", qty=150, unit="g"),
                    PlanItem(name="egg whole", qty=100, unit="g"),
                    PlanItem(name="eggs", qty=1, unit="item"),
                ],
            )
        ]
        db.add(plan)
        db.add(
            GroceryPreference(
                user_id=user.id,
                ingredient_key="whole egg",
                in_pantry=True,
                preferred_brand="Saved brand",
                package_quantity=12,
                package_unit="item",
            )
        )
        db.commit()
        before = [(i.id, i.name, i.qty, i.unit, i.meta) for i in plan.meals[0].items]
        result = plans.grocery_list_week(start=plan.date, end=plan.date, db=db, user=user)
        assert len(result["items"]) == 1
        item = result["items"][0]
        assert item["id"] == "egg"
        assert (item["quantity"], item["unit"]) == (6, "item")
        assert item["preference"]["in_pantry"] is True
        assert item["preference"]["preferred_brand"] == "Saved brand"
        assert item["package"]["source"] == "preferred"
        db.expire_all()
        assert [(i.id, i.name, i.qty, i.unit, i.meta) for i in plan.meals[0].items] == before


def test_ready_to_eat_eggs_keep_their_own_count_and_product_identity():
    key = plans._grocery_name("hard-boiled eggs")[0]
    assert key != plans._grocery_name("raw eggs")[0]
    assert plans._format_grocery_measurements(key, {"mass_g": 150}, "US")["quantity"] == 3
    assert (
        plans._grocery_name("purchased ready-to-eat firm tofu")[0] == plans._grocery_name("ready-to-eat firm tofu")[0]
    )
    assert plans._grocery_name("ready-to-eat firm tofu")[0] != plans._grocery_name("firm tofu")[0]


@pytest.mark.parametrize("name", ["russet potato with skin", "tofu firm prepared with calcium sulfate ready to eat"])
def test_food_labels_preserve_their_identity_and_aisle(name):
    key, label = plans._grocery_name(name)
    assert plans._grocery_name(label)[0] == key
    assert plans._grocery_category(label) != "Other"


def test_all_new_display_labels_preserve_identity():
    for key, label in plans._GROCERY_DISPLAY_NAMES.items():
        assert plans._grocery_name(label)[0] == key
