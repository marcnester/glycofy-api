#!/usr/bin/env python3
"""
scripts/db_repair.py — idempotent SQLite schema repair for local dev

Fixes:
- Ensures `activities` table exists with required columns
- Ensures `oauth_accounts` table exists with required columns
- When ALTER-adding columns, strips ALL DEFAULT/NOT NULL (SQLite limitation),
  then backfills as needed (e.g., created_at timestamps, source_provider='manual').
- Creates helpful indexes
- Optionally seeds a sample activity if `activities` is empty (for dev).

Usage:
  python scripts/db_repair.py               # detects db path (app.db/glycofy.db/…)
  python scripts/db_repair.py ./glycofy.db  # explicit path
"""

import os
import sqlite3
import sys

# ---------- Desired schemas ----------

REQUIRED_ACTIVITIES_COLUMNS: dict[str, str] = {
    "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
    "user_id": "INTEGER NOT NULL",
    "sport": "TEXT",
    "start_time": "TEXT",  # ISO8601
    "duration_s": "INTEGER",  # seconds
    "kcal": "INTEGER",
    "distance_m": "INTEGER",
    "source_provider": "TEXT",  # 'strava' | 'manual' | …
    "source_id": "TEXT",  # external id
    "created_at": "TEXT DEFAULT (CURRENT_TIMESTAMP) NOT NULL",
}

OAUTH_ACCOUNTS_CREATE = """
CREATE TABLE IF NOT EXISTS oauth_accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  provider TEXT NOT NULL,
  external_athlete_id TEXT,
  access_token TEXT,
  refresh_token TEXT,
  expires_at INTEGER,
  scope TEXT,
  created_at TEXT DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
  updated_at TEXT DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
  UNIQUE(user_id, provider)
);
"""

REQUIRED_OAUTH_SOFT = {
    # Columns we will ensure (ALTER-safe types only; no defaults on ALTER)
    "user_id": "INTEGER",
    "provider": "TEXT",
    "access_token": "TEXT",
    "refresh_token": "TEXT",
    "expires_at": "INTEGER",
    "created_at": "TEXT",
    "updated_at": "TEXT",
}

# ---------- Helpers ----------


def get_db_path() -> str:
    if len(sys.argv) >= 2:
        return sys.argv[1]
    url = os.getenv("DATABASE_URL") or os.getenv("DB_URL") or ""
    if url.startswith("sqlite:///"):
        return url.replace("sqlite:///", "", 1)
    if url.startswith("sqlite://"):
        return url.replace("sqlite://", "", 1)
    # common local names
    for candidate in ("glycofy.db", "app.db", "data.db"):
        if os.path.exists(candidate):
            return candidate
    return "glycofy.db"


def table_exists(cur, name: str) -> bool:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?;", (name,))
    return cur.fetchone() is not None


def get_columns(cur, table: str) -> dict:
    cur.execute(f"PRAGMA table_info({table});")
    cols = {}
    for cid, name, ctype, notnull, dflt, pk in cur.fetchall():
        cols[name] = {"type": ctype or "", "notnull": notnull, "default": dflt}
    return cols


def strip_defaults_and_notnull(decl: str) -> str:
    """
    Remove ALL DEFAULT and NOT NULL tokens from a column declaration string
    so it becomes ALTER-friendly for SQLite. Keep only the base type/constraints
    that are safe for ALTER (e.g., INTEGER, TEXT, etc.).
    """
    tokens = decl.replace("(", " ( ").replace(")", " ) ").split()
    out = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        tu = t.upper()
        if tu == "DEFAULT":
            # skip DEFAULT <anything>, including parenthesized expressions
            i += 1
            # consume a value/expression (best-effort): if it starts with '(' consume until ')'
            if i < len(tokens) and tokens[i].startswith("("):
                depth = 0
                while i < len(tokens):
                    for ch in tokens[i]:
                        if ch == "(":
                            depth += 1
                        elif ch == ")":
                            depth -= 1
                    i += 1
                    if depth <= 0:
                        break
            else:
                # consume single literal/identifier
                i += 1
            continue
        if tu == "NOT" and i + 1 < len(tokens) and tokens[i + 1].upper() == "NULL":
            i += 2
            continue
        out.append(t)
        i += 1
    # collapse extra spaces
    normalized = " ".join(out)
    # trim trailing commas or stray parens if any shenanigans
    normalized = normalized.strip().strip(",")
    return normalized


def alter_add_column(cur, table: str, col: str, decl: str):
    """
    ALTER-add a column safely by stripping DEFAULT/NOT NULL.
    """
    safe_decl = strip_defaults_and_notnull(decl)
    # SQLite requires at least a type; if decl was empty after stripping, default to TEXT
    if not safe_decl or safe_decl.upper() in {"PRIMARY", "PRIMARY KEY"}:
        safe_decl = "TEXT"
    cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {safe_decl};")


def ensure_activities(conn):
    cur = conn.cursor()
    if not table_exists(cur, "activities"):
        # On CREATE TABLE, we can keep defaults.
        parts = [f"{col} {ctype}" for col, ctype in REQUIRED_ACTIVITIES_COLUMNS.items()]
        ddl = "CREATE TABLE activities (\n  " + ",\n  ".join(parts) + "\n);"
        cur.execute(ddl)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_activities_user ON activities(user_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_activities_start ON activities(start_time);")
        print("✔ created table: activities")
    else:
        existing = get_columns(cur, "activities")
        for col, decl in REQUIRED_ACTIVITIES_COLUMNS.items():
            if col not in existing:
                alter_add_column(cur, "activities", col, decl)
                print(f"✔ added column activities.{col}")
        # Backfills
        # source_provider -> 'manual' if NULL
        cur.execute("UPDATE activities SET source_provider='manual' WHERE source_provider IS NULL;")
        # created_at -> CURRENT_TIMESTAMP if NULL
        cur.execute("UPDATE activities SET created_at=CURRENT_TIMESTAMP WHERE created_at IS NULL;")
        # Indexes
        cur.execute("CREATE INDEX IF NOT EXISTS idx_activities_user ON activities(user_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_activities_start ON activities(start_time);")
        print("✔ verified/updated table: activities")


def ensure_oauth_accounts(conn):
    cur = conn.cursor()
    if not table_exists(cur, "oauth_accounts"):
        cur.executescript(OAUTH_ACCOUNTS_CREATE)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_oauth_user_id ON oauth_accounts(user_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_oauth_provider ON oauth_accounts(provider);")
        print("✔ created table: oauth_accounts")
    else:
        cols = get_columns(cur, "oauth_accounts")
        for col, decl in REQUIRED_OAUTH_SOFT.items():
            if col not in cols:
                alter_add_column(cur, "oauth_accounts", col, decl)
                print(f"✔ added column oauth_accounts.{col}")
        # Backfill timestamps if present but NULL
        try:
            cur.execute("UPDATE oauth_accounts SET created_at=CURRENT_TIMESTAMP WHERE created_at IS NULL;")
        except sqlite3.OperationalError:
            pass
        try:
            cur.execute("UPDATE oauth_accounts SET updated_at=CURRENT_TIMESTAMP WHERE updated_at IS NULL;")
        except sqlite3.OperationalError:
            pass
        cur.execute("CREATE INDEX IF NOT EXISTS idx_oauth_user_id ON oauth_accounts(user_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_oauth_provider ON oauth_accounts(provider);")
        print("✔ verified/updated table: oauth_accounts")


def maybe_seed(conn):
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM activities;")
        count = cur.fetchone()[0]
    except Exception:
        return
    if count == 0:
        cur.execute(
            "INSERT INTO activities (user_id, sport, start_time, duration_s, kcal, distance_m, source_provider) "
            "VALUES (1, 'Running', datetime('now','-1 day'), 3600, 800, 10000, 'manual');"
        )
        print("✔ seeded one sample activity")


def main():
    db_path = get_db_path()
    print(f"→ repairing schema in: {db_path}")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys=OFF;")
        ensure_activities(conn)
        ensure_oauth_accounts(conn)
        maybe_seed(conn)
        conn.commit()
        print("✅ schema repair complete")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
