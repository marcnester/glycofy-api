from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Plan, PlanItem, PlanMeal, User
from app.routers import llm_recommend, plans


@pytest.mark.parametrize(
    "alias,canonical",
    [
        ("Whole egg", "egg"),
        ("eggs whole raw", "egg"),
        ("russet potato with skin", "russet potatoes raw with skin"),
        ("mozzarella cheese whole milk", "whole milk mozzarella"),
        ("dry couscous", "couscous dry"),
        ("couscous", "couscous dry"),
        ("canned tuna in water, drained", "canned tuna in water"),
        ("nonfat Greek yogurt", "plain nonfat greek yogurt"),
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
        ("dry couscous", "cooked couscous"),
        ("canned tuna in water", "canned tuna in oil"),
        ("plain nonfat Greek yogurt", "Greek yogurt plain 2% milkfat"),
        ("almond butter", "peanut butter"),
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


@pytest.mark.parametrize("routine_collision", [False, True])
def test_easy_week_plans_separate_simple_lunches_and_repeats_fresh_breakfasts(monkeypatch, routine_collision):
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
    if routine_collision:
        generated[days[2]]["snack"].ai_idea["title"] = generated[days[0]]["breakfast"].ai_idea["title"]
        # Catalog recovery must not reintroduce tuna into a missing snack.
        tuna_candidate = _recommendation("snack", "Canned Tuna Snack")
        tuna_candidate.ai_idea["ingredients"][0]["name"] = "canned tuna in water"
        monkeypatch.setattr(
            llm_recommend, "_recommend_for_single_meal", lambda **_kwargs: tuna_candidate.model_copy(deep=True)
        )
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
        if not routine_collision:
            snacks = {
                row["date"]: next(item for item in row["items"] if item["slot"] == "snack") for row in result["days"]
            }
            assert snacks[days[3]]["ai_idea"]["title"] == snacks[days[0]]["ai_idea"]["title"]
            assert snacks[days[2]]["ai_idea"]["title"] != snacks[days[0]]["ai_idea"]["title"]
        for i, day in enumerate(days):
            plan = db.query(Plan).filter(Plan.user_id == user.id, Plan.date == date.fromisoformat(day)).one()
            assert plans._aggregate_totals(plan.meals)["kcal"] == pytest.approx(426 * 4, abs=1)
            assert len({meal.title for meal in plan.meals}) == len(plan.meals)
            lunch = next(meal for meal in plan.meals if meal.meal_type == "lunch")
            assert lunch.title == f"Day {i} tofu rice wrap"
            assert "leftovers" not in (lunch.meta or {})
            assert lunch.instructions == "Warm the cooked rice and tofu.\nCombine and serve."
            assert (lunch.meta or {}).get("total_time_min") == 5


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


@pytest.mark.parametrize(
    "meal,expected",
    [
        ({"title": "Leftover salmon bowl"}, True),
        ({"instructions": ["Reheat yesterday's dinner."]}, True),
        ({"instructions": ["Use the reserved dinner portion."]}, True),
        ({"instructions": ["Toast bread. Assemble a fresh tuna sandwich."]}, False),
        ({"instructions": ["Warm ready-cooked rice. Refrigerate any leftovers promptly."]}, False),
    ],
)
def test_fresh_meals_reject_dependencies_without_rejecting_storage_advice(meal, expected):
    assert llm_recommend._requires_prepared_meal(meal) is expected


def test_easy_generation_uses_bounded_calls_with_actual_shared_basket(monkeypatch):
    calls = []
    monkeypatch.setenv("USDA_FDC_WEEKLY_SYNC_LOOKUPS", "0")
    monkeypatch.setenv("WEEKLY_DAY_MAX_WORKERS", "1")
    days = [{"date": f"2026-11-0{i}", "planning_style": "easy", "meals": []} for i in (2, 3, 4)]

    def generate(_client, **kwargs):
        calls.append(kwargs)
        assert len(kwargs["days"]) == 1
        assert kwargs["synchronous_lookup_limit"] == 0
        day = kwargs["days"][0]
        return {day["date"]: {"breakfast": _recommendation("breakfast", "Fresh Tofu Rice Toast")}}, {
            "accepted": 1,
            "rejected": 0,
        }

    monkeypatch.setattr(llm_recommend, "_batch_week_recommendations", generate)
    result, meta = llm_recommend._parallel_week_recommendations(
        None, days=days, primary_diet="pescatarian", diet_tags=[], exclusions=[]
    )
    assert set(result) == {day["date"] for day in days}
    assert meta["mode"] == "shared_basket_days"
    assert len(calls) == 3
    assert calls[0]["days"][0]["shopping_context"]["allow_tuna"] is False
    for call in calls[1:]:
        context = call["days"][0]["shopping_context"]
        assert context["preferred_ingredients"] == ["cooked brown rice", "firm tofu"]
        assert context["fresh_routine_recipes"][0]["recipe"]["title"] == "Fresh Tofu Rice Toast"

    assert "tofu" in days[1]["shopping_context"]["dinner_direction"]
    assert "cod" in days[2]["shopping_context"]["dinner_direction"]
    assert days[1]["shopping_context"] == calls[1]["days"][0]["shopping_context"]


def test_easy_week_assigns_one_tuna_lunch_and_six_snack_formats():
    days = [llm_recommend._easy_day_directions(i, "pescatarian") for i in range(7)]
    assert sum(day["allow_tuna"] for day in days) == 1
    assert len({day["lunch_direction"] for day in days}) == 7
    assert len({day[key] for day in days for key in ("snack_direction", "snack_2_direction")}) == 6
    assert all("no seafood" in day[key] for day in days for key in ("snack_direction", "snack_2_direction"))
    assert not any(llm_recommend._easy_day_directions(i, "vegan")["allow_tuna"] for i in range(7))
    assert llm_recommend._contains_tuna({"ingredients": [{"name": "canned tuna in water"}]})
    assert not llm_recommend._contains_tuna({"ingredients": [{"name": "white beans"}]})


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
