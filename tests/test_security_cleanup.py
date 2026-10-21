from __future__ import annotations

import ast
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import Plan, User
from app.observability import JsonFormatter
from app.rate_limit import AUTH_LIMITER
from app.routers import llm_recommend, plans, weekly_plans
from scripts.check_code_scanning import blocking_alerts


@pytest.fixture()
def cleanup_app():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    def override_db():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    AUTH_LIMITER.clear()
    try:
        with TestClient(app) as client:
            yield client, engine
    finally:
        app.dependency_overrides.clear()
        AUTH_LIMITER.clear()
        engine.dispose()


def signup(client, email="cleanup@example.com"):
    client.cookies.clear()
    response = client.post("/auth/signup", json={"email": email, "password": "a-secure-password-123"})
    assert response.status_code == 200


def test_legacy_week_requires_authentication_and_rejects_query_injection(cleanup_app, monkeypatch):
    client, _ = cleanup_app
    assert client.post("/v1/plan/week/generate").status_code == 401
    signup(client)

    async def forbidden(**kwargs):
        raise AssertionError("Invalid input must not reach planning")

    monkeypatch.setattr(weekly_plans, "get_plan", forbidden)
    for invalid in ["llm&replace=true", "https://attacker.invalid/", "llm#fragment", "../profile"]:
        assert client.post("/v1/plan/week/generate", params={"engine": invalid}).status_code == 422
    assert client.post("/v1/plan/week/generate", params={"start": "9999-12-31", "days": 2}).status_code == 422


def test_legacy_week_uses_internal_owner_scoped_plans_and_keeps_existing(cleanup_app, monkeypatch):
    client, engine = cleanup_app
    monkeypatch.setattr(plans, "_seed_plan_heuristic", lambda *args: {"totals": {}, "meals": []})
    signup(client)
    params = {"engine": "heuristic", "start": "2026-10-20", "days": 2}
    first = client.post("/v1/plan/week/generate", params=params)
    assert first.status_code == 200
    with Session(engine) as db:
        original_ids = {row.id for row in db.query(Plan).all()}
    assert client.post("/v1/plan/week/generate", params=params).status_code == 200
    with Session(engine) as db:
        assert {row.id for row in db.query(Plan).all()} == original_ids
    assert client.post("/v1/plan/week/generate", params=params).status_code == 429
    signup(client, "second-cleanup@example.com")
    assert client.post("/v1/plan/week/generate", params={**params, "replace": True}).status_code == 200
    with Session(engine) as db:
        assert db.query(Plan).count() == 4
        first_user = db.query(User).filter_by(email="cleanup@example.com").one()
        assert {row.id for row in db.query(Plan).filter_by(user_id=first_user.id)} == original_ids


def test_legacy_week_does_not_disclose_provider_errors(cleanup_app, monkeypatch):
    client, _ = cleanup_app
    signup(client)

    async def provider_failure(**kwargs):
        raise RuntimeError("secret-token-and-private-athlete-data")

    monkeypatch.setattr(weekly_plans, "get_plan", provider_failure)
    response = client.post("/v1/plan/week/generate", params={"days": 1})
    assert response.status_code == 502
    assert response.json()["detail"]["results"][0]["error"] == "daily_plan_failed"
    assert "secret-token" not in response.text


def test_preferences_and_planner_logs_exclude_sensitive_values(cleanup_app, caplog):
    client, _ = cleanup_app
    signup(client)
    caplog.set_level(logging.INFO)
    sensitive = "private-exclusion-marker\nFORGED_LOG_ENTRY"
    assert client.put("/v1/preferences", json={"diet": "vegan", "ingredient_exclusions": sensitive}).status_code == 200
    assert client.get("/v1/preferences").status_code == 200
    pref = SimpleNamespace(diet_type="private-diet-marker", user_id=1, ingredient_exclusions=sensitive)
    llm_recommend._diet_tags_from_preferences(pref)
    llm_recommend._filter_by_diet_tags([], ["private-diet-marker"], "private-diet-marker")
    for marker in ("private-exclusion-marker", "private-diet-marker", "FORGED_LOG_ENTRY"):
        assert marker not in caplog.text


def test_exception_formatter_keeps_locations_without_messages_or_values():
    try:
        raise RuntimeError("private-token-or-sql-parameter")
    except RuntimeError:
        record = logging.LogRecord("test", logging.ERROR, __file__, 1, "operation_failed", (), sys.exc_info())
    encoded = JsonFormatter().format(record)
    payload = json.loads(encoded)
    assert "private-token-or-sql-parameter" not in encoded
    assert payload["exception"]["type"] == "RuntimeError"
    assert (
        payload["exception"]["frames"][-1]["function"]
        == "test_exception_formatter_keeps_locations_without_messages_or_values"
    )


def test_sensitive_modules_use_fixed_log_events_without_interpolated_values():
    for filename in ("preferences.py", "llm_recommend.py"):
        tree = ast.parse(Path("app/routers", filename).read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "logger"
            ):
                assert len(node.args) == 1, f"{filename}:{node.lineno} interpolates log values"
                assert isinstance(node.args[0], ast.Constant)
                for keyword in node.keywords:
                    if keyword.arg == "extra" and isinstance(keyword.value, ast.Dict):
                        keys = {key.value for key in keyword.value.keys if isinstance(key, ast.Constant)}
                        assert not keys & {
                            "target_totals",
                            "actual_totals",
                            "diet_tags",
                            "ingredient_exclusions",
                            "ip",
                            "payload",
                        }


def test_security_gate_blocks_high_and_critical_but_not_closed_or_contextual_alerts():
    def alert(number, severity, state="open"):
        return {"number": number, "state": state, "rule": {"security_severity_level": severity}}

    pages = [[alert(1, "critical"), alert(2, "high"), alert(3, "medium")], [alert(4, "high", "dismissed")]]
    assert [item["number"] for item in blocking_alerts(pages)] == [1, 2]


def test_oauth_key_rotation_rehearsal_and_atomic_rollback(monkeypatch):
    from cryptography.fernet import Fernet, InvalidToken
    from sqlalchemy import text

    from app.config import settings
    from app.encrypted_types import EncryptedText
    from scripts.rotate_oauth_tokens import rotate_credentials

    old_key, new_key = Fernet.generate_key(), Fernet.generate_key()
    old_cipher, new_cipher = Fernet(old_key), Fernet(new_key)
    engine = create_engine("sqlite://")
    original = "gfy1:" + old_cipher.encrypt(b"test-provider-token").decode()
    with engine.begin() as db:
        db.execute(text("CREATE TABLE oauth_accounts (id INTEGER PRIMARY KEY, access_token TEXT, refresh_token TEXT)"))
        db.execute(text("INSERT INTO oauth_accounts VALUES (1, :token, NULL)"), {"token": original})
    monkeypatch.setattr(settings, "OAUTH_TOKEN_ENCRYPTION_KEY", new_key.decode())
    monkeypatch.setattr(settings, "OAUTH_TOKEN_PREVIOUS_ENCRYPTION_KEYS", old_key.decode())
    encrypted = EncryptedText()
    assert encrypted.process_result_value(original, None) == "test-provider-token"
    assert rotate_credentials(engine) == {"accounts": 1, "credentials": 1}
    with engine.connect() as db:
        assert db.execute(text("SELECT access_token FROM oauth_accounts WHERE id=1")).scalar_one() == original
    with engine.begin() as db:
        db.execute(text("INSERT INTO oauth_accounts VALUES (2, 'gfy1:corrupt', NULL)"))
    with pytest.raises(InvalidToken):
        rotate_credentials(engine, apply=True)
    with engine.connect() as db:
        assert db.execute(text("SELECT access_token FROM oauth_accounts WHERE id=1")).scalar_one() == original
    with engine.begin() as db:
        db.execute(text("DELETE FROM oauth_accounts WHERE id=2"))
    assert rotate_credentials(engine, apply=True)["credentials"] == 1
    with engine.connect() as db:
        stored = db.execute(text("SELECT access_token FROM oauth_accounts WHERE id=1")).scalar_one()
    assert new_cipher.decrypt(stored.removeprefix("gfy1:").encode()) == b"test-provider-token"
    monkeypatch.setattr(settings, "OAUTH_TOKEN_PREVIOUS_ENCRYPTION_KEYS", "")
    assert encrypted.process_result_value(stored, None) == "test-provider-token"


@pytest.mark.parametrize(
    "value",
    [
        "//attacker.invalid",
        "/\\attacker.invalid",
        "javascript:alert(1)",
        "https://attacker.invalid",
        "/auth/logout",
        "/ui/%2f%2fattacker.invalid",
        "\n//attacker.invalid",
    ],
)
def test_server_redirects_reject_external_and_non_ui_targets(value):
    from app.redirects import safe_return_path
    from app.routers import oauth_google, oauth_strava

    assert safe_return_path(value) == "/ui/index.html"
    assert oauth_google._safe_return_path(value) == "/ui/index.html"
    assert oauth_strava._safe_return_path(value) is None
    assert safe_return_path("/ui/profile.html?unused=value") == "/ui/profile.html"


def test_browser_redirects_use_constant_allowlisted_destinations():
    import shutil
    import subprocess

    if not shutil.which("node"):
        pytest.skip("Node required for browser redirect regression test")
    script = r"""
const fs = require("fs"), vm = require("vm"), assert = require("assert");
for (const file of ["ui/login.js", "ui/core.js", "ui/app.js"]) {
  const source = fs.readFileSync(file, "utf8");
  const fn = source.match(/function (safePath|sanitizeReturnPath)\(p\) \{[\s\S]*?\n  \}/)[0];
  const context = vm.createContext({});
  const safe = vm.runInContext("(" + fn + ")", context);
  for (const value of ["//attacker.invalid", "/\\attacker.invalid", "javascript:alert(1)", "/auth/logout", "https://attacker.invalid", "\n//attacker.invalid", "/%5cattacker.invalid"]) {
    assert.equal(safe(value), "/ui/index.html", file + " rejects " + value);
  }
  assert.equal(safe("/ui/profile.html?unused=value"), "/ui/profile.html");
}
"""
    subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)


def test_csv_validation_does_not_echo_exception_values():
    from datetime import date

    from app.routers.training_events import _parse_trainingpeaks_csv

    rows, errors, _ = _parse_trainingpeaks_csv(
        b"WorkoutDay,WorkoutTitle,Duration,DistanceKm\n2099-01-01,PrivateWorkout,01:00:00,private-token-marker\n",
        today=date(2026, 10, 20),
    )
    assert rows == []
    assert errors
    assert "private-token-marker" not in str(errors)


def test_parallel_language_gates_do_not_read_other_language_stale_results():
    python_alert = {
        "number": 1,
        "state": "open",
        "rule": {"security_severity_level": "high"},
        "most_recent_instance": {"category": "/language:python"},
    }
    javascript_alert = {
        "number": 2,
        "state": "open",
        "rule": {"security_severity_level": "critical"},
        "most_recent_instance": {"category": "/language:javascript-typescript"},
    }
    pages = [[python_alert, javascript_alert]]
    assert blocking_alerts(pages, "/language:python") == [python_alert]
    assert blocking_alerts(pages, "/language:javascript-typescript") == [javascript_alert]
    assert blocking_alerts([[python_alert]], "/language:javascript-typescript") == []
    assert blocking_alerts(pages) == [python_alert, javascript_alert]


def test_ai_provider_errors_do_not_escape_in_response_metadata(monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("private-provider-token-and-payload")

    monkeypatch.setattr(llm_recommend, "_circuit_open", lambda: False)
    monkeypatch.setattr(llm_recommend, "_daily_budget_usd", lambda: float("inf"))
    monkeypatch.setattr(llm_recommend, "_record_failure", lambda: None)
    monkeypatch.setattr(llm_recommend, "_sleep_backoff", lambda attempt: None)
    monkeypatch.setattr(llm_recommend, "record_ai_operation", lambda *args, **kwargs: None)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fail)))
    result, meta = llm_recommend._safe_openai_json_pick(client, "test-model", "system", {})
    assert result is None
    assert meta["error"] == "RuntimeError"
    assert "private-provider-token" not in str(meta)


def test_strava_fetch_error_is_safe_for_authenticated_caller(cleanup_app, monkeypatch):
    from app.routers import oauth_strava

    client, _ = cleanup_app
    signup(client)
    monkeypatch.setattr(oauth_strava, "_configured", lambda: True)
    monkeypatch.setattr(oauth_strava, "_load_strava_account", lambda *args: SimpleNamespace(access_token="test-token"))
    monkeypatch.setattr(oauth_strava, "_refresh_if_needed", lambda *args: "test-token")
    monkeypatch.setattr(oauth_strava, "_latest_strava_activity_at", lambda *args: None)

    def fail(*args, **kwargs):
        raise RuntimeError("private-provider-token-and-payload")

    monkeypatch.setattr(oauth_strava, "_fetch_all_strava_activities", fail)
    response = client.post("/sync/strava")
    assert response.status_code == 502
    assert response.json()["detail"] == "Unable to fetch Strava activities. Please retry."
    assert "private-provider-token" not in response.text
