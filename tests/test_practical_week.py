from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Plan, PlanItem, PlanMeal, User
from app.routers import llm_recommend, plans
from app.services.meal_preparation import link_week_leftovers, preparation_for_meal


@pytest.mark.parametrize(
    "alias,canonical",
    [
        ("Whole egg", "egg"),
        ("Egg whole", "egg"),
        ("eggs", "egg"),
        ("cod fillet, raw", "cod fillet"),
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
        ("plain nonfat Greek yogurt", "Greek yogurt plain 2% milkfat"),
        ("almond butter", "peanut butter"),
    ]:
        assert plans._grocery_name(a)[0] != plans._grocery_name(b)[0]


@pytest.mark.parametrize(
    "name,category",
    [
        ("black pepper", "Pantry"),
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


def _recommendation(slot, title, minutes=5):
    ingredients = [
        {
            "name": "firm tofu",
            "amount": 150.0,
            "amount_g": 150.0,
            "unit": "g",
            "nutrition": {"kcal": 180.0, "protein_g": 20.0, "carbs_g": 4.0, "fat_g": 10.0},
        },
        {
            "name": "cooked brown rice",
            "amount": 200.0,
            "amount_g": 200.0,
            "unit": "g",
            "nutrition": {"kcal": 246.0, "protein_g": 5.0, "carbs_g": 51.0, "fat_g": 2.0},
        },
    ]
    idea = {
        "title": title,
        "ingredients": ingredients,
        "instructions": ["Warm the cooked rice and tofu.", "Combine and serve."],
        "prep_time_min": 2,
        "cook_time_min": minutes - 2,
        "total_time_min": minutes,
        "protein_group": "plant",
        "protein_item": "tofu",
        "carb_item": "brown rice",
        "approx_macros": llm_recommend.ingredient_nutrition_totals({"ingredients": ingredients}),
    }
    return llm_recommend.SlotRecommendation(
        slot=slot, target=idea["approx_macros"], ai_idea=idea, meta={"ai_idea": idea}
    )


def test_easy_week_allows_routine_while_varied_week_keeps_its_guard():
    days = [{"date": f"2026-11-0{i}", "planning_style": "easy"} for i in (2, 3, 4)]
    items = {day["date"]: {"breakfast": _recommendation("breakfast", "Yogurt Bowl")} for day in days}
    assert llm_recommend._deduplicate_week_batch(days, items)[1] == 0
    for day in days:
        day["planning_style"] = "varied"
    assert llm_recommend._deduplicate_week_batch(days, items)[1] == 2
    assert llm_recommend.WeeklyRecommendRequest().planning_style == "easy"


def test_leftover_portions_preserve_cooked_dish_ratios_and_exclusions():
    source = _recommendation("dinner", "Tofu Rice Bowl", 30)
    target = llm_recommend.MealTarget(slot="lunch", kcal=340, protein_g=20, carbs_g=44, fat_g=10)
    kwargs = dict(
        target_date="2026-11-03",
        target=target,
        primary_diet="vegan",
        exclusions=[],
        meal_key_counts={},
        day_meal_keys=set(),
        source_slot="dinner",
        max_minutes=45,
    )
    repeated = llm_recommend._reuse_verified_week_meal({"2026-11-02": {"dinner": source}}, **kwargs)
    assert repeated is not None
    ratios = [
        a["amount_g"] / b["amount_g"]
        for a, b in zip(repeated.ai_idea["ingredients"], source.ai_idea["ingredients"], strict=True)
    ]
    assert ratios[0] == pytest.approx(ratios[1])
    assert repeated.ai_idea["approx_macros"]["kcal"] == pytest.approx(340, abs=1)
    kwargs["exclusions"] = ["soy"]
    assert llm_recommend._reuse_verified_week_meal({"2026-11-02": {"dinner": source}}, **kwargs) is None


def test_slow_breakfast_cannot_be_reused_as_an_easy_breakfast():
    source = _recommendation("breakfast", "Slow Breakfast", 30)
    assert (
        llm_recommend._reuse_verified_week_meal(
            {"2026-11-02": {"breakfast": source}},
            target_date="2026-11-03",
            target=llm_recommend.MealTarget(slot="breakfast", kcal=426, protein_g=25, carbs_g=55, fat_g=12),
            primary_diet="vegan",
            exclusions=[],
            meal_key_counts={},
            day_meal_keys=set(),
            max_minutes=10,
        )
        is None
    )


@pytest.mark.parametrize("changed", ["source", "consumer", "deleted_source"])
def test_leftover_links_persist_count_groceries_once_and_invalidate_after_edits(changed):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(email="prep@example.com", password_hash="test")
        db.add(user)
        db.flush()
        meals = []
        for offset, slot, grams in [(0, "dinner", 200), (1, "lunch", 150)]:
            plan = Plan(user_id=user.id, date=date(2026, 11, 2) + timedelta(days=offset), locked=False)
            meal = PlanMeal(
                plan=plan,
                meal_type=slot,
                title="Tofu bowl",
                instructions="Cook tofu. Serve.",
                items=[PlanItem(name="tofu", qty=grams, unit="g")],
            )
            db.add(plan)
            meals.append(meal)
        db.flush()
        dinner, lunch = meals
        finalized = {"2026-11-03": {"lunch": SimpleNamespace(meta={"leftover_source_date": "2026-11-02"})}}
        link_week_leftovers(db, user.id, finalized, {"2026-11-02", "2026-11-03"})
        db.commit()
        db.expire_all()
        assert preparation_for_meal(lunch)["kind"] == "leftovers"
        batch = preparation_for_meal(dinner)
        assert batch["batch_ingredients"] == [{"name": "tofu", "quantity": 350, "unit": "g"}]
        assert "43%" in batch["storage"]
        grocery = plans.grocery_list_week(start=date(2026, 11, 2), end=date(2026, 11, 3), db=db, user=user)
        assert len(grocery["items"]) == 1
        assert grocery["items"][0]["quantity"] == pytest.approx(350 / 28.3495, abs=0.1)
        assert plans._plan_to_dict(lunch.plan)["meals"][0]["preparation"]["kind"] == "leftovers"
        if changed == "source":
            dinner.items[0].qty = 400
        elif changed == "consumer":
            lunch.instructions = "Changed meal"
        else:
            db.delete(dinner)
            db.flush()
        assert preparation_for_meal(lunch) is None


def test_easy_week_orchestration_repeats_fast_templates_links_lunch_and_preserves_macros(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    days = [(date(2026, 11, 2) + timedelta(days=i)).isoformat() for i in range(4)]
    slots = ["breakfast", "lunch", "dinner", "snack"]
    generated = {
        day: {
            slot: _recommendation(
                slot,
                f"Day {i} tofu rice "
                + {"breakfast": "toast", "lunch": "wrap", "dinner": "skillet", "snack": "cup"}[slot],
                30 if slot == "dinner" else 5,
            )
            for slot in slots
        }
        for i, day in enumerate(days)
    }
    monkeypatch.setattr(llm_recommend, "_parallel_week_recommendations", lambda *_a, **_k: (generated, {}))
    monkeypatch.setattr(llm_recommend, "_get_openai_client", lambda: None)
    monkeypatch.setattr(llm_recommend._RATE, "check_and_add", lambda *_args: None)
    # Isolate meal scheduling from the independently tested athlete target calculator.
    monkeypatch.setattr(llm_recommend, "_targets_for_preferences", lambda day, _pref: day.meals)
    monkeypatch.setattr(llm_recommend, "_apply_nutrition_targets", lambda meals, _nutrition: meals)
    targets = {"kcal": 426, "protein_g": 25, "carbs_g": 55, "fat_g": 12}
    with Session(engine) as db:
        user = User(email="easy-week@example.com", password_hash="test")
        db.add(user)
        db.commit()
        payload = llm_recommend.WeeklyRecommendRequest(
            days=[
                llm_recommend.WeeklyDayRequest(
                    date=day, meals=[llm_recommend.MealTarget(slot=slot, **targets) for slot in slots]
                )
                for day in days
            ]
        )
        result = llm_recommend.recommend_weekly_apply(
            SimpleNamespace(client=SimpleNamespace(host="127.0.0.1")), payload, db, user
        )
        assert result["generation"]["planning_style"] == "easy"
        breakfasts = {
            row["date"]: next(item for item in row["items"] if item["slot"] == "breakfast") for row in result["days"]
        }
        assert breakfasts[days[2]]["ai_idea"]["title"] == breakfasts[days[0]]["ai_idea"]["title"]
        assert breakfasts[days[3]]["ai_idea"]["title"] == breakfasts[days[1]]["ai_idea"]["title"]
        for i, day in enumerate(days):
            plan = db.query(Plan).filter(Plan.user_id == user.id, Plan.date == date.fromisoformat(day)).one()
            assert plans._aggregate_totals(plan.meals)["kcal"] == pytest.approx(426 * 4, abs=1)
            lunch = next(meal for meal in plan.meals if meal.meal_type == "lunch")
            if i:
                assert preparation_for_meal(lunch)["source_date"] == days[i - 1]
            else:
                assert preparation_for_meal(lunch) is None


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
        result = plans.grocery_list_week(start=plan.date, end=plan.date, db=db, user=user)
        assert len(result["items"]) == 1
        item = result["items"][0]
        assert item["id"] == "egg"
        assert (item["quantity"], item["unit"]) == (6, "item")
        assert item["preference"]["in_pantry"] is True
        assert item["preference"]["preferred_brand"] == "Saved brand"
        assert item["package"]["source"] == "preferred"
