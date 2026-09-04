#!/usr/bin/env python3
"""Read-only verification for a database restored from a production backup."""

from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy import create_engine, inspect, text

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

REQUIRED_TABLES = {"alembic_version", "users", "plans", "plan_meals", "weekly_planning_jobs", "ai_operation_metrics"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.getenv("RESTORE_DATABASE_URL"))
    args = parser.parse_args()
    if not args.database_url:
        parser.error("pass --database-url or set RESTORE_DATABASE_URL")
    url = args.database_url
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(url, pool_pre_ping=True)
    expected_head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    with engine.connect() as connection:
        missing = sorted(REQUIRED_TABLES - set(inspect(connection).get_table_names()))
        current = MigrationContext.configure(connection).get_current_revision()
        if missing:
            print(f"FAIL missing tables: {', '.join(missing)}", file=sys.stderr)
            return 1
        if current != expected_head:
            print(f"FAIL migration revision {current!r}; expected {expected_head!r}", file=sys.stderr)
            return 1
        counts = {
            table: connection.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one()
            for table in sorted(REQUIRED_TABLES - {"alembic_version"})
        }
        connection.execute(text("SELECT id, status FROM weekly_planning_jobs ORDER BY created_at DESC LIMIT 1")).all()
    print(f"PASS restored database is readable at Alembic head {expected_head}")
    print("Row counts: " + ", ".join(f"{name}={count}" for name, count in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
