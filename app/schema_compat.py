from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.db import engine

logger = logging.getLogger(__name__)

SNACK_PREFERENCE_COLUMNS = frozenset({"daily_snack_count", "snack_times"})


def ensure_snack_preference_columns(db_engine: Engine = engine) -> set[str]:
    """Repair the narrowly scoped snack-preference rollout on PostgreSQL.

    Alembic remains the schema source of truth. This compatibility guard exists
    so a web service whose pre-deploy command was skipped cannot serve a newer
    ORM model against the immediately preceding production schema.
    """
    if db_engine.dialect.name != "postgresql":
        return set()

    with db_engine.begin() as connection:
        existing = {column["name"] for column in inspect(connection).get_columns("user_preferences")}
        missing = SNACK_PREFERENCE_COLUMNS - existing
        if "daily_snack_count" in missing:
            connection.execute(
                text("ALTER TABLE user_preferences " "ADD COLUMN IF NOT EXISTS daily_snack_count INTEGER")
            )
        if "snack_times" in missing:
            connection.execute(text("ALTER TABLE user_preferences " "ADD COLUMN IF NOT EXISTS snack_times JSON"))

    if missing:
        logger.warning("Repaired missing snack preference columns: %s", sorted(missing))
    return set(missing)
