from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy.orm import Session

from app.models import MealFeedback


def feedback_context(db: Session, user_id: int, limit: int = 60) -> dict[str, Any]:
    """Return a small, non-identifying preference summary for meal generation."""
    rows = (
        db.query(MealFeedback)
        .filter(MealFeedback.user_id == user_id)
        .order_by(MealFeedback.updated_at.desc())
        .limit(limit)
        .all()
    )
    if not rows:
        return {"feedback_count": 0}

    favorites = Counter(
        row.meal_title for row in rows if row.outcome == "eaten" and row.rating is not None and row.rating >= 4
    )
    avoid = Counter(
        row.meal_title for row in rows if row.outcome == "skipped" or (row.rating is not None and row.rating <= 2)
    )
    return {
        "feedback_count": len(rows),
        "favorite_meals": [title for title, _ in favorites.most_common(6)],
        "avoid_repeating": [title for title, _ in avoid.most_common(6)],
        "portion_signals": dict(Counter(row.portion for row in rows if row.portion)),
        "hunger_signals": dict(Counter(row.hunger_after for row in rows if row.hunger_after)),
        "energy_signals": dict(Counter(row.energy_after for row in rows if row.energy_after)),
        "digestion_signals": dict(Counter(row.digestion for row in rows if row.digestion)),
        "practicality_signals": dict(Counter(row.practicality for row in rows if row.practicality)),
    }
