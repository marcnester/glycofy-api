from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.auth_utils import get_current_user
from app.db import get_db
from app.models import User
from app.models_plan import ActivityDailySummary, PlanDay, PlanFeedback
from app.services.llm_meals import propose_meals_simple
from app.services.plan_writer import upsert_plan
from app.services.targets import allocate_per_meal, compute_targets
from app.services.training_load import rebuild_daily_summaries

router = APIRouter(prefix="/v1/plan", tags=["plan"])


def _parse_date(s: str | None) -> date:
    if not s:
        return date.today()
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid date format; expected YYYY-MM-DD")


def _serialize_plan(plan: PlanDay) -> dict[str, Any]:
    return {
        "id": plan.id,
        "user_id": plan.user_id,
        "date": plan.date.isoformat(),
        "locked": bool(plan.locked),
        "targets": plan.targets or {},
        "totals": plan.totals or {},
        "window_used": plan.window_used,
        "meals": [
            {
                "slot": m.slot,
                "title": m.title,
                "kcal": m.kcal,
                "protein_g": m.protein_g,
                "carbs_g": m.carbs_g,
                "fat_g": m.fat_g,
                "instructions": m.instructions,
                "items": [
                    {
                        "name": it.name,
                        "qty": float(it.qty) if it.qty is not None else None,
                        "unit": it.unit,
                        "kcal": it.kcal,
                        "protein_g": it.protein_g,
                        "carbs_g": it.carbs_g,
                        "fat_g": it.fat_g,
                    }
                    for it in m.items
                ],
            }
            for m in sorted(plan.meals, key=lambda mm: mm.rank or 0)
        ],
    }


@router.get("/{d}")
def get_plan(
    d: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    on = _parse_date(d)
    plan = db.query(PlanDay).filter(PlanDay.user_id == user.id, PlanDay.date == on).first()
    if not plan:
        raise HTTPException(status_code=404, detail="No plan for this day")
    return _serialize_plan(plan)


@router.get("/targets/{d}")
def get_targets(
    d: str,
    window: int = Query(5, ge=3, le=7),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    on = _parse_date(d)
    t = compute_targets(db, user.id, on, window)
    return {
        "date": on.isoformat(),
        "window_used": t.meta["window_used"],
        "exercise_kcal": t.meta["exercise_kcal"],
        "intensity_hint": t.meta["intensity_hint"],
        "targets": {
            "kcal": t.kcal,
            "protein_g": t.protein_g,
            "carbs_g": t.carbs_g,
            "fat_g": t.fat_g,
            "hydration_ml": t.hydration_ml,
            "fiber_g": t.fiber_g,
        },
    }


@router.post("/{d}/generate")
def generate_plan(
    d: str,
    body: dict[str, Any] = Body(
        default={"window": 5, "overwrite": False, "strategy": "auto", "meal_count": 4, "llm": True}
    ),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    on = _parse_date(d)
    overwrite = bool(body.get("overwrite", False))
    meal_count = int(body.get("meal_count", 4))
    window = int(body.get("window", 5))

    # Respect lock unless overwrite
    existing = db.query(PlanDay).filter(PlanDay.user_id == user.id, PlanDay.date == on).first()
    if existing and existing.locked and not overwrite:
        return _serialize_plan(existing)

    # Ensure daily summary exists for window
    rebuild_daily_summaries(db, user.id, on - timedelta(days=window - 1), on)

    # Compute targets & per-meal allocations
    t = compute_targets(db, user.id, on, window)
    alloc = allocate_per_meal(t, meal_count)

    # Call LLM (placeholder → simple template bank for now)
    meals = propose_meals_simple(on.isoformat(), alloc)

    # Persist
    plan = upsert_plan(
        db=db,
        user_id=user.id,
        on_date=on,
        targets={
            "kcal": t.kcal,
            "protein_g": t.protein_g,
            "carbs_g": t.carbs_g,
            "fat_g": t.fat_g,
            "hydration_ml": t.hydration_ml,
            "fiber_g": t.fiber_g,
        },
        window_used=window,
        meals_json=meals,
    )
    return _serialize_plan(plan)


@router.post("/{d}/lock")
def lock_plan(
    d: str,
    lock: bool = Query(default=True),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    on = _parse_date(d)
    plan = db.query(PlanDay).filter(PlanDay.user_id == user.id, PlanDay.date == on).first()
    if not plan:
        raise HTTPException(status_code=404, detail="No plan for this day")
    plan.locked = bool(lock)
    db.commit()
    db.refresh(plan)
    return {"locked": plan.locked}


@router.post("/{d}/feedback")
def submit_feedback(
    d: str,
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    on = _parse_date(d)
    plan = db.query(PlanDay).filter(PlanDay.user_id == user.id, PlanDay.date == on).first()
    if not plan:
        raise HTTPException(status_code=404, detail="No plan for this day")
    fb = PlanFeedback(
        user_id=user.id,
        plan_day_id=plan.id,
        rating=int(payload.get("rating") or 0),
        tags=payload.get("tags") or [],
        comment=(payload.get("comment") or "")[:4000],
    )
    db.add(fb)
    db.commit()
    return {"ok": True}


# ── Convenience rollup + debug endpoint ───────────────────────────────────────


@router.post("/../activities/summary/rebuild")
def activities_rollup_rebuild(
    start: str | None = Query(None),
    end: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = _parse_date(start) if start else (date.today() - timedelta(days=7))
    e = _parse_date(end) if end else date.today()
    count = rebuild_daily_summaries(db, user.id, s, e)
    return {"days_written": count, "range": [s.isoformat(), e.isoformat()]}


@router.get("/../activities/summary")
def activities_summary(
    start: str | None = Query(None),
    end: str | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = _parse_date(start) if start else (date.today() - timedelta(days=6))
    e = _parse_date(end) if end else date.today()
    rows = (
        db.query(ActivityDailySummary)
        .filter(ActivityDailySummary.user_id == user.id)
        .filter(ActivityDailySummary.date >= s)
        .filter(ActivityDailySummary.date <= e)
        .order_by(ActivityDailySummary.date.asc())
        .all()
    )
    return [
        {
            "date": r.date.isoformat(),
            "kcal_exercise": r.kcal_exercise,
            "duration_min": r.duration_min,
            "intensity_hint": r.intensity_hint,
            "sport_breakdown": r.sport_breakdown or {},
        }
        for r in rows
    ]


# ── Grocery exports (minimal stubs; you can keep your existing ones) ──────────


@router.get("/{d}/grocery.txt", response_class=PlainTextResponse)
def grocery_txt(
    d: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    on = _parse_date(d)
    plan = db.query(PlanDay).filter(PlanDay.user_id == user.id, PlanDay.date == on).first()
    if not plan:
        raise HTTPException(status_code=404, detail="No plan")
    lines: list[str] = []
    for m in plan.meals:
        lines.append(f"# {m.slot.capitalize()} — {m.title}")
        for it in m.items:
            qty = f" {it.qty}{it.unit}" if it.qty and it.unit else ""
            lines.append(f"- {it.name}{qty}")
        lines.append("")
    return "\n".join(lines).strip()
