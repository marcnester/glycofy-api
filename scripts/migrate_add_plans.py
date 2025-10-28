# scripts/migrate_add_plans.py
from __future__ import annotations
import sqlite3
from app.config import settings

DDL = [
    # plans
    """
    CREATE TABLE IF NOT EXISTS plans (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL,
        date DATE NOT NULL,
        diet_pref VARCHAR(32),
        locked BOOLEAN NOT NULL DEFAULT 0,
        tdee_kcal INTEGER NOT NULL,
        training_kcal INTEGER NOT NULL,
        protein_g INTEGER NOT NULL,
        carbs_g INTEGER NOT NULL,
        fat_g INTEGER NOT NULL,
        created_at DATETIME NOT NULL DEFAULT (datetime('now')),
        updated_at DATETIME NOT NULL DEFAULT (datetime('now')),
        UNIQUE(user_id, date),
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_plan_user_date ON plans(user_id, date);",
    # plan_meals
    """
    CREATE TABLE IF NOT EXISTS plan_meals (
        id INTEGER PRIMARY KEY,
        plan_id INTEGER NOT NULL,
        order_index INTEGER NOT NULL DEFAULT 0,
        title VARCHAR(200) NOT NULL,
        meal_type VARCHAR(20) NOT NULL,
        diet_tags VARCHAR(120) NOT NULL,
        kcal INTEGER NOT NULL,
        protein_g INTEGER NOT NULL,
        carbs_g INTEGER NOT NULL,
        fat_g INTEGER NOT NULL,
        ingredients TEXT NOT NULL,
        instructions TEXT NOT NULL,
        created_at DATETIME NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY(plan_id) REFERENCES plans(id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS ix_plan_meal_plan ON plan_meals(plan_id);",
]

def main():
    dsn = settings.database_url
    if not dsn.startswith("sqlite:///"):
        print("This helper only supports sqlite:/// DSNs.")
        return 0
    path = dsn.replace("sqlite:///", "")
    conn = sqlite3.connect(path)
    try:
        for stmt in DDL:
            conn.execute(stmt)
        conn.commit()
        print("✅ Plans tables ready.")
    finally:
        conn.close()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())