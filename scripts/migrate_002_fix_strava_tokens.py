#!/usr/bin/env python3
"""
Fix strava_tokens schema for Glycofy (add missing primary key id).
- Works on SQLite.
- Preserves existing data.
"""

import os
import sqlite3
from contextlib import closing

DB_PATH = os.environ.get("DATABASE_URL", "sqlite:///./glycofy.db")
if DB_PATH.startswith("sqlite:///"):
    DB_FILE = DB_PATH.replace("sqlite:///", "", 1)
elif DB_PATH.startswith("sqlite://"):
    DB_FILE = DB_PATH.replace("sqlite://", "", 1)
else:
    # For non-sqlite usage, exit (this script is for SQLite only)
    raise SystemExit("This migration script is for SQLite only.")

def table_exists(cur, name: str) -> bool:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?;", (name,))
    return cur.fetchone() is not None

def columns(cur, name: str):
    cur.execute(f"PRAGMA table_info({name});")
    return [row[1] for row in cur.fetchall()]

def main():
    print(f"[migrate] opening {DB_FILE}")
    with closing(sqlite3.connect(DB_FILE)) as con:
        con.isolation_level = None  # autocommit off for BEGIN..COMMIT
        cur = con.cursor()

        if not table_exists(cur, "strava_tokens"):
            print("[migrate] strava_tokens not found; creating fresh table")
            cur.execute("BEGIN")
            cur.execute("""
                CREATE TABLE strava_tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_sub TEXT NOT NULL,
                    access_token TEXT NOT NULL,
                    refresh_token TEXT,
                    expires_at INTEGER NOT NULL,
                    athlete_id BIGINT,
                    scope TEXT,
                    updated_at TEXT
                );
            """)
            cur.execute("CREATE INDEX idx_strava_tokens_user_sub ON strava_tokens (user_sub);")
            cur.execute("COMMIT")
            print("[migrate] done (created)")
            return

        cols = columns(cur, "strava_tokens")
        if "id" in cols:
            print("[migrate] strava_tokens already has id; nothing to do.")
            return

        print("[migrate] BEGIN")
        cur.execute("BEGIN")

        # Create the new table with the correct schema.
        cur.execute("""
            CREATE TABLE strava_tokens_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_sub TEXT NOT NULL,
                access_token TEXT NOT NULL,
                refresh_token TEXT,
                expires_at INTEGER NOT NULL,
                athlete_id BIGINT,
                scope TEXT,
                updated_at TEXT
            );
        """)

        # Derive the list of available columns in the old table (except id which doesn’t exist).
        # We copy what exists; missing columns will get NULL/defaults.
        copy_cols = [c for c in cols if c in {
            "user_sub","access_token","refresh_token","expires_at","athlete_id","scope","updated_at"
        }]

        if not copy_cols:
            # No columns? Just drop/rename.
            copy_cols_sql = ""
        else:
            copy_cols_sql = ", ".join(copy_cols)
            cur.execute(f"""
                INSERT INTO strava_tokens_new ({copy_cols_sql})
                SELECT {copy_cols_sql} FROM strava_tokens;
            """)

        # Swap tables
        cur.execute("DROP TABLE strava_tokens;")
        cur.execute("ALTER TABLE strava_tokens_new RENAME TO strava_tokens;")
        cur.execute("CREATE INDEX idx_strava_tokens_user_sub ON strava_tokens (user_sub);")

        cur.execute("COMMIT")
        print("[migrate] done")

if __name__ == "__main__":
    main()