# app/routers/dashboard.py
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth_utils import get_current_user
from app.db import get_db
from app.models import User  # NOTE: avoid Activity ORM (schema drift)

router = APIRouter()


def _load_meal_plan(db: Session, uid: int, day: date) -> list[dict[str, Any]]:
    row = db.execute(
        text("SELECT items FROM meal_plan WHERE user_id = :uid AND date = :d"),
        {"uid": uid, "d": day},
    ).first()
    if not row or not row[0]:
        return []
    try:
        items = json.loads(row[0])
        return items if isinstance(items, list) else []
    except Exception:
        return []


def _fabricate_meals_from_recipes(db: Session) -> list[dict[str, Any]]:
    # pick up to one recipe per meal_type in this order
    out: list[dict[str, Any]] = []
    for mt in ("breakfast", "lunch", "dinner", "snack"):
        r = db.execute(
            text(
                """
                SELECT title, meal_type, kcal, protein_g, carbs_g, fat_g, ingredients, instructions
                FROM recipes
                WHERE LOWER(meal_type) = :mt
                LIMIT 1
            """
            ),
            {"mt": mt},
        ).first()
        if r:
            title, meal_type, kcal, pg, cg, fg, ing, instr = r
            try:
                ingredients = json.loads(ing) if isinstance(ing, (str, bytes)) else (ing or [])
            except Exception:
                ingredients = []
            out.append(
                {
                    "meal_type": (meal_type or mt).lower(),
                    "title": title,
                    "kcal": int(kcal or 0),
                    "protein_g": int(pg or 0),
                    "carbs_g": int(cg or 0),
                    "fat_g": int(fg or 0),
                    "ingredients": ingredients if isinstance(ingredients, list) else [],
                    "instructions": instr or "",
                }
            )
    return out


def _rollup_totals(meals: list[dict[str, Any]]) -> dict[str, int]:
    t = {"kcal": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0}
    for m in meals:
        t["kcal"] += int(m.get("kcal") or 0)
        t["protein_g"] += int(m.get("protein_g") or 0)
        t["carbs_g"] += int(m.get("carbs_g") or 0)
        t["fat_g"] += int(m.get("fat_g") or 0)
    return t


@router.get("/today")
def today_summary(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Unified dashboard payload for the current day:
      - account
      - nutrition: totals + targets + meals (from meal_plan; falls back to recipes)
      - activities: last-7d rollup + latest 5 (SQL, not ORM, to avoid column drift)
    """
    today = date.today()

    # --------- Meals (meal_plan or fabricate from recipes) ---------
    meals = _load_meal_plan(db, user.id, today)
    if not meals:
        meals = _fabricate_meals_from_recipes(db)
    totals = _rollup_totals(meals)

    # --------- Targets from daily_nutrition (optional) ---------
    dn = db.execute(
        text(
            """
            SELECT training_kcal, tdee_kcal, protein_g, carbs_g, fat_g
            FROM daily_nutrition
            WHERE user_id = :uid AND date = :d
        """
        ),
        {"uid": user.id, "d": today},
    ).first()

    targets = None
    if dn:
        training_kcal, tdee_kcal, p, c, f = dn
        targets = {
            "training_kcal": int(training_kcal or 0),
            "tdee_kcal": int(tdee_kcal or 0),
            "protein_g": int(p or 0),
            "carbs_g": int(c or 0),
            "fat_g": int(f or 0),
        }

    # --------- Activities (last 7 days) via SQL-only ---------
    since = datetime.utcnow() - timedelta(days=7)

    latest_rows = db.execute(
        text(
            """
            SELECT start_time, sport, kcal, distance_m, duration_s, provider
            FROM activities
            WHERE user_id = :uid AND start_time >= :since
            ORDER BY start_time DESC
            LIMIT 5
        """
        ),
        {"uid": user.id, "since": since},
    ).fetchall()

    latest = []
    for r in latest_rows:
        started, sport, kcal, distance_m, duration_s, provider = r
        # Ensure ISO-ish string for the UI
        if hasattr(started, "isoformat"):
            started = started.isoformat()
        else:
            started = str(started)
        latest.append(
            {
                "started": started,
                "sport": sport,
                "kcal": float(kcal) if kcal is not None else 0.0,
                "distance_m": float(distance_m) if distance_m is not None else 0.0,
                "duration_s": int(duration_s) if duration_s is not None else 0,
                "provider": provider,
            }
        )

    roll_row = db.execute(
        text(
            """
            SELECT
                COUNT(1) AS cnt,
                COALESCE(SUM(kcal), 0) AS sum_kcal,
                COALESCE(SUM(distance_m), 0) AS sum_dist,
                COALESCE(SUM(duration_s), 0) AS sum_dur
            FROM activities
            WHERE user_id = :uid AND start_time >= :since
        """
        ),
        {"uid": user.id, "since": since},
    ).first()

    act_rollup = {
        "count": int(roll_row.cnt if hasattr(roll_row, "cnt") else roll_row[0]),
        "kcal": int(roll_row.sum_kcal if hasattr(roll_row, "sum_kcal") else roll_row[1]),
        "distance_m": float(roll_row.sum_dist if hasattr(roll_row, "sum_dist") else roll_row[2]),
        "duration_s": int(roll_row.sum_dur if hasattr(roll_row, "sum_dur") else roll_row[3]),
    }

    return {
        "account": {"user_id": user.id, "email": user.email},
        "nutrition": {"date": str(today), "totals": totals, "targets": targets, "meals": meals},
        "activities": {"last7d": act_rollup, "latest": latest},
    }
