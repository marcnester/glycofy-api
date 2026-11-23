import hashlib
import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base
from app.models import Plan, User, UserPreference
from app.routers import llm_recommend as planner
from app.routers.plans import _plan_nutrition_verified
from app.services.meal_quality import validate_meal


def fingerprint(plan):
    def row(obj):
        return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}

    return hashlib.sha256(
        json.dumps(
            {
                "plan": row(plan),
                "meals": [{"meal": row(m), "items": [row(i) for i in m.items]} for m in plan.meals],
            },
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()


@pytest.mark.parametrize(("lock_first", "extra_exclusion"), [(False, ""), (True, ""), (False, "sunflower")])
def test_sparse_vegan_wheat_free_week_recovers_verified_meals_and_preserves_locks(
    monkeypatch, lock_first, extra_exclusion
):
    monkeypatch.setattr(settings, "USDA_LOCAL_CATALOG_ENABLED", True)
    monkeypatch.setattr(planner, "_get_openai_client", lambda: object())
    monkeypatch.setattr(planner, "_parallel_week_recommendations", lambda *a, **k: ({}, {"mode": "empty"}))
    monkeypatch.setattr(planner, "_batch_week_recommendations", lambda *a, **k: ({}, {}))
    monkeypatch.setattr(planner._RATE, "check_and_add", lambda *a: None)
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    start = date(2027, 3, 1)
    slots = ("breakfast", "lunch", "dinner", "snack", "snack_2")
    payload = planner.WeeklyRecommendRequest(
        days=[
            planner.WeeklyDayRequest(
                date=str(start + timedelta(days=n)),
                meals=[planner.MealTarget(slot=s, kcal=480, protein_g=30, carbs_g=56, fat_g=14.8) for s in slots],
            )
            for n in range(7)
        ]
    )
    with Session(engine) as db:
        user = User(email="recovery@example.test", password_hash="unused")
        db.add(user)
        db.flush()
        db.add(
            UserPreference(
                user_id=user.id,
                diet_type="vegan",
                ingredient_exclusions="wheat," + extra_exclusion,
                daily_snack_count=2,
            )
        )
        db.commit()
        if extra_exclusion:
            with pytest.raises(HTTPException) as error:
                planner.recommend_weekly_apply(SimpleNamespace(client=SimpleNamespace(host="test")), payload, db, user)
            assert error.value.detail["reason"] == "restricted_week_incomplete"
            assert db.query(Plan).count() == 0
            return
        # Seed a real, fully verified day before locking it; no persistence mocks.
        if lock_first:
            targets = {s: {"kcal": 480, "protein_g": 30, "carbs_g": 56, "fat_g": 14.8} for s in slots}
            from app.services.vegan_meal_options import curated_menu_options

            menu = curated_menu_options(0, list(slots), include_fish=False)
            for idea in menu.values():
                idea["approx_macros"] = planner.ingredient_nutrition_totals(idea)
            items = [planner.SlotRecommendation(slot=s, target=targets[s], ai_idea=menu[s]) for s in slots]
            planner._persist_day_recommendations(db, user, str(start), items, ["vegan"], "vegan")
            db.commit()
            locked = db.query(Plan).filter_by(user_id=user.id, date=start).one()
            locked.locked = True
            db.commit()
            before = fingerprint(locked)
            assert _plan_nutrition_verified(locked.meals)
        result = planner.recommend_weekly_apply(SimpleNamespace(client=SimpleNamespace(host="test")), payload, db, user)
        assert result["generation"]["verified_week_recovery"] == {"missing_before_recovery": 35, "accepted": 35}
        plans = db.query(Plan).filter_by(user_id=user.id).order_by(Plan.date).all()
        assert len(plans) == 7 and sum(len(p.meals) for p in plans) == 35
        for plan in plans:
            assert _plan_nutrition_verified(plan.meals)
        for day in result["days"]:
            for item in day["items"]:
                report = validate_meal(
                    {**item["ai_idea"], "slot": item["slot"]},
                    diet="vegan",
                    exclusions=["wheat"],
                    require_ingredient_nutrition=True,
                )
                assert report.safe, report.codes()
        if lock_first:
            db.refresh(locked)
            assert fingerprint(locked) == before
            assert result["persist"]["applied"][0]["skipped"] is True
