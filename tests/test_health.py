from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.db import _set_sqlite_pragma
from app.main import app
from app.schema_compat import ensure_snack_preference_columns


def test_liveness_and_database_readiness() -> None:
    with TestClient(app) as client:
        live = client.get("/health")
        ready = client.get("/ready")

    assert live.status_code == 200
    assert live.json()["status"] == "ok"
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"


def test_sqlite_pragma_hook_ignores_non_sqlite_connections() -> None:
    class NonSqliteConnection:
        def cursor(self):
            raise AssertionError("SQLite PRAGMA must not run on PostgreSQL")

    _set_sqlite_pragma(NonSqliteConnection(), None)


def test_snack_schema_compatibility_guard_is_postgresql_only() -> None:
    sqlite_engine = create_engine("sqlite:///:memory:")

    assert ensure_snack_preference_columns(sqlite_engine) == set()
