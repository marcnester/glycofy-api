#!/usr/bin/env python3
import os
import sqlite3
import sys

DB_PATH = os.getenv("DATABASE_URL", "sqlite:///./glycofy.db")
if DB_PATH.startswith("sqlite:///"):
    DB_PATH = DB_PATH.replace("sqlite:///", "", 1)

print(f"[migrate] opening {DB_PATH}")
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()


def get_columns(table):
    cur.execute(f"PRAGMA table_info({table});")
    return {row["name"]: row for row in cur.fetchall()}


def table_exists(table):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?;", (table,))
    return cur.fetchone() is not None


def index_exists(name):
    cur.execute("SELECT name FROM sqlite_master WHERE type='index' AND name=?;", (name,))
    return cur.fetchone() is not None


def current_shape_ok():
    if not table_exists("activities"):
        return False
    cols = get_columns("activities")
    needed = [
        "id",
        "user_sub",
        "strava_id",
        "name",
        "type",
        "start_time",
        "duration_sec",
        "distance_m",
        "kcal",
    ]
    return all(c in cols for c in needed)


try:
    print("[migrate] BEGIN")
    cur.execute("PRAGMA foreign_keys=OFF;")
    cur.execute("BEGIN TRANSACTION;")

    if not table_exists("activities"):
        print("[migrate] activities does not exist — creating fresh table")
        cur.execute(
            """
            CREATE TABLE activities (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_sub TEXT NOT NULL,
              strava_id INTEGER NOT NULL,
              name TEXT NOT NULL,
              type TEXT NOT NULL,
              start_time TEXT NOT NULL,
              duration_sec INTEGER NOT NULL,
              distance_m INTEGER NOT NULL,
              kcal INTEGER NOT NULL DEFAULT 0
            );
        """
        )
    elif not current_shape_ok():
        print("[migrate] rebuilding activities table to new schema")
        # Detect if old table has strava_id
        old_cols = get_columns("activities")
        has_strava_id = "strava_id" in old_cols
        # Build a new table
        cur.execute(
            """
            CREATE TABLE activities_new (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_sub TEXT NOT NULL,
              strava_id INTEGER NOT NULL,
              name TEXT NOT NULL,
              type TEXT NOT NULL,
              start_time TEXT NOT NULL,
              duration_sec INTEGER NOT NULL,
              distance_m INTEGER NOT NULL,
              kcal INTEGER NOT NULL DEFAULT 0
            );
        """
        )
        # Compose copy statement
        # Map strava_id: prefer old.strava_id; else old.id
        # Map kcal: prefer old.kcal; else 0
        src_strava = "strava_id" if has_strava_id else "id"
        src_kcal = "kcal" if "kcal" in old_cols else "0"
        # Name/type/start_time/duration_sec/distance_m fall back to sensible defaults if missing
        name_col = "name" if "name" in old_cols else "('Workout')"
        type_col = "type" if "type" in old_cols else "('Workout')"
        start_col = "start_time" if "start_time" in old_cols else "('')"
        dur_col = "duration_sec" if "duration_sec" in old_cols else "(0)"
        dist_col = "distance_m" if "distance_m" in old_cols else "(0)"
        # Copy
        sql = f"""
            INSERT INTO activities_new (user_sub, strava_id, name, type, start_time, duration_sec, distance_m, kcal)
            SELECT
              'user:demo@glycofy.app' as user_sub,
              COALESCE({src_strava}, 0) as strava_id,
              COALESCE({name_col}, 'Workout') as name,
              COALESCE({type_col}, 'Workout') as type,
              COALESCE({start_col}, '') as start_time,
              COALESCE({dur_col}, 0) as duration_sec,
              COALESCE({dist_col}, 0) as distance_m,
              COALESCE({src_kcal}, 0) as kcal
            FROM activities;
        """
        cur.execute(sql)
        # Swap tables
        cur.execute("DROP TABLE activities;")
        cur.execute("ALTER TABLE activities_new RENAME TO activities;")
    else:
        print("[migrate] activities already in new shape, nothing to do.")

    # Indexes (create if missing)
    if not index_exists("uq_user_strava"):
        print("[migrate] CREATE UNIQUE INDEX uq_user_strava ON activities (user_sub, strava_id);")
        cur.execute("CREATE UNIQUE INDEX uq_user_strava ON activities (user_sub, strava_id);")
    else:
        print("[migrate] uq_user_strava exists.")

    if not index_exists("ix_user_start"):
        print("[migrate] CREATE INDEX ix_user_start ON activities (user_sub, start_time);")
        cur.execute("CREATE INDEX ix_user_start ON activities (user_sub, start_time);")
    else:
        print("[migrate] ix_user_start exists.")

    cur.execute("COMMIT;")
    cur.execute("PRAGMA foreign_keys=ON;")
    print("[migrate] COMMIT OK")
except Exception as e:
    try:
        cur.execute("ROLLBACK;")
    except Exception:
        pass
    print("[migrate] ERROR:", e)
    sys.exit(1)
finally:
    conn.close()
    print("[migrate] done")
