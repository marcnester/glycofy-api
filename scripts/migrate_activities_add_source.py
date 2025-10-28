# scripts/migrate_activities_add_source.py
from __future__ import annotations
import sqlite3
from app.config import settings

def column_exists(conn, table, column):
    cur = conn.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in cur.fetchall()]
    return column in cols

def index_exists(conn, table, name):
    cur = conn.execute(f"PRAGMA index_list({table})")
    idx = [r[1] for r in cur.fetchall()]
    return name in idx

def main():
    dsn = settings.database_url
    if not dsn.startswith("sqlite:///"):
        print("This helper only supports sqlite:/// DSNs.")
        return 0

    path = dsn.replace("sqlite:///", "")
    conn = sqlite3.connect(path)
    try:
        if not column_exists(conn, "activities", "source_provider"):
            print("Adding column activities.source_provider ...")
            conn.execute("ALTER TABLE activities ADD COLUMN source_provider TEXT")
        if not column_exists(conn, "activities", "source_id"):
            print("Adding column activities.source_id ...")
            conn.execute("ALTER TABLE activities ADD COLUMN source_id TEXT")
        conn.commit()

        if not index_exists(conn, "activities", "ux_activities_source"):
            print("Creating unique index ux_activities_source ...")
            conn.execute(
                "CREATE UNIQUE INDEX ux_activities_source "
                "ON activities (user_id, source_provider, source_id)"
            )
            conn.commit()

        print("✅ Migration complete.")
    finally:
        conn.close()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())