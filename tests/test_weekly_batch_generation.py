import json
from types import SimpleNamespace

from app.routers import llm_recommend


def _meal(slot: str, day: int) -> dict:
    protein = f"protein-{day}-{slot}"
    carb = f"carb-{day}-{slot}"
    dish = {"breakfast": "oat bowl", "lunch": "grain salad", "dinner": "vegetable skillet", "snack": "fruit parfait"}[
        slot
    ]
    return {
        "slot": slot,
        "title": f"Day {day} {dish}",
        "ingredients": [
            {"name": protein, "amount": "150", "unit": "g"},
            {"name": carb, "amount": "1", "unit": "cup"},
            {"name": "spinach", "amount": "2", "unit": "cups"},
            {"name": "olive oil", "amount": "1", "unit": "tbsp"},
        ],
        "instructions": ["Cook the protein and carbohydrate.", "Combine and serve."],
        "protein_group": "plant" if day == 1 else "poultry",
        "protein_item": protein,
        "carb_item": carb,
        "macros": {"kcal": 500, "protein_g": 40, "carbs_g": 50, "fat_g": 15},
        "reason": "Balanced for the target.",
    }


def test_weekly_batch_uses_one_structured_call_and_accepts_complete_week(monkeypatch):
    dates = ["2026-09-01", "2026-09-02"]
    response_body = {
        "days": [
            {"date": date, "meals": [_meal(slot, day) for slot in llm_recommend.SLOTS]}
            for day, date in enumerate(dates, start=1)
        ]
    }
    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(response_body)))],
                usage=SimpleNamespace(model_dump=lambda: {"prompt_tokens": 100, "completion_tokens": 200}),
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    days = [
        {
            "date": date,
            "training": {},
            "diet_tags": [],
            "meals": [
                {
                    "slot": slot,
                    "target_macros": {"kcal": 500, "protein_g": 40, "carbs_g": 50, "fat_g": 15},
                }
                for slot in llm_recommend.SLOTS
            ],
        }
        for date in dates
    ]

    monkeypatch.setattr(llm_recommend, "_circuit_open", lambda: False)
    monkeypatch.setattr(llm_recommend, "_daily_budget_usd", lambda: 100.0)
    recommendations, meta = llm_recommend._batch_week_recommendations(
        client,
        days=days,
        primary_diet="omnivore",
        diet_tags=[],
        exclusions=[],
    )

    assert len(calls) == 1
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert meta["accepted"] == 8
    assert meta["rejected"] == 0
    assert all(set(recommendations[date]) == set(llm_recommend.SLOTS) for date in dates)
    assert all(item.meta["batch"] is True for slots in recommendations.values() for item in slots.values())


def test_weekly_job_status_is_scoped_to_owner():
    job_id = "owner-scoped-job"
    llm_recommend._WEEKLY_JOBS[job_id] = {
        "job_id": job_id,
        "user_id": 42,
        "status": "completed",
        "stage": "completed",
        "message": "Ready",
        "completed_days": 7,
        "total_days": 7,
        "elapsed_seconds": 12.5,
        "result": {"days": []},
        "error": None,
    }
    try:
        owner = SimpleNamespace(id=42)
        response = llm_recommend.weekly_job_status(job_id, owner)
        assert response.status == "completed"

        stranger = SimpleNamespace(id=99)
        try:
            llm_recommend.weekly_job_status(job_id, stranger)
        except llm_recommend.HTTPException as exc:
            assert exc.status_code == 404
        else:
            raise AssertionError("another user could read the weekly job")
    finally:
        llm_recommend._WEEKLY_JOBS.pop(job_id, None)


def test_weekly_batch_rejects_a_meal_with_bad_macros(monkeypatch):
    date = "2026-09-01"
    meals = [_meal(slot, 1) for slot in llm_recommend.SLOTS]
    meals[0]["macros"]["carbs_g"] = 2
    response_body = {"days": [{"date": date, "meals": meals}]}

    class Completions:
        def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(response_body)))],
                usage=None,
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    days = [
        {
            "date": date,
            "training": {},
            "diet_tags": [],
            "meals": [
                {
                    "slot": slot,
                    "target_macros": {"kcal": 500, "protein_g": 40, "carbs_g": 50, "fat_g": 15},
                }
                for slot in llm_recommend.SLOTS
            ],
        }
    ]
    monkeypatch.setattr(llm_recommend, "_circuit_open", lambda: False)
    monkeypatch.setattr(llm_recommend, "_daily_budget_usd", lambda: 100.0)

    recommendations, meta = llm_recommend._batch_week_recommendations(
        client,
        days=days,
        primary_diet="omnivore",
        diet_tags=[],
        exclusions=[],
    )

    assert "breakfast" not in recommendations[date]
    assert meta["accepted"] == 3
    assert meta["rejected"] == 1
