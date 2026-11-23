import asyncio
import logging
import threading
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Activity, OAuthAccount, User
from app.services import auto_sync


def test_sync_only_connected_strava_accounts_and_isolates_failures(monkeypatch, caplog):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(auto_sync, "SessionLocal", sessions)
    with sessions() as db:
        user_ids = []
        for index, (provider, token) in enumerate(
            [("google", "token"), ("strava", None), ("strava", ""), ("strava", "first"), ("strava", "second")]
        ):
            user = User(email=f"sync-{index}@example.invalid", password_hash="!disabled-test")
            db.add(user)
            db.flush()
            user_ids.append(user.id)
            db.add(OAuthAccount(user_id=user.id, provider=provider, access_token=token))
        db.commit()
    calls = []

    def sync(db, user, since):
        calls.append((user.id, since))
        if len(calls) == 1:
            raise RuntimeError("sensitive-provider-token")
        return {"linked": True, "created": 1}

    monkeypatch.setattr(auto_sync, "sync_strava", sync)
    with caplog.at_level(logging.INFO, logger="glycofy.auto_sync"):
        auto_sync._sync_accounts()
    assert [user_id for user_id, _ in calls] == user_ids[3:]
    assert all(since == date.fromisoformat(auto_sync._month_start_utc_iso()) for _, since in calls)
    assert "sensitive-provider-token" not in caplog.text
    assert [r.status for r in caplog.records if r.name == "glycofy.auto_sync"] == ["failed", "completed"]
    assert not auto_sync._SYNC_LOCK.locked()
    engine.dispose()


def test_provider_error_is_not_logged_as_success(monkeypatch, caplog):
    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def query(self, *args):
            return self

        def filter(self, *args):
            return self

        def all(self):
            return [SimpleNamespace(id=1)]

        def get(self, model, key):
            return SimpleNamespace(access_token="token", user_id=1) if model is OAuthAccount else object()

    monkeypatch.setattr(auto_sync, "SessionLocal", DB)
    monkeypatch.setattr(auto_sync, "sync_strava", lambda *args: {"linked": True, "error": "token_invalid"})
    with caplog.at_level(logging.INFO, logger="glycofy.auto_sync"):
        auto_sync._sync_accounts()
    assert [r.status for r in caplog.records if r.name == "glycofy.auto_sync"] == ["provider_unavailable"]


def test_sync_offloads_blocking_work_without_blocking_event_loop(monkeypatch):
    monkeypatch.setenv("AUTO_SYNC_JITTER_SECS", "0")
    started = threading.Event()
    release = threading.Event()
    worker_threads = []

    def blocking():
        worker_threads.append(threading.get_ident())
        started.set()
        assert release.wait(3)

    monkeypatch.setattr(auto_sync, "_sync_accounts", blocking)

    async def run():
        task = asyncio.create_task(auto_sync._sync_once())
        try:
            assert await asyncio.to_thread(started.wait, 2)
            assert not task.done()
            assert worker_threads != [threading.get_ident()]
        finally:
            release.set()
            await task

    asyncio.run(run())


def test_overlapping_pass_is_skipped(monkeypatch):
    monkeypatch.setattr(auto_sync, "SessionLocal", lambda: (_ for _ in ()).throw(AssertionError("overlap")))
    auto_sync._SYNC_LOCK.acquire()
    try:
        auto_sync._sync_accounts()
    finally:
        auto_sync._SYNC_LOCK.release()


def test_lifecycle_starts_once_and_stops_promptly():
    async def run():
        loop = asyncio.get_running_loop()
        auto_sync.start_auto_sync_loop(loop)
        task = auto_sync._TASK
        auto_sync.start_auto_sync_loop(loop)
        assert auto_sync._TASK is task
        await auto_sync.stop_auto_sync_loop()
        assert task.done()
        assert auto_sync._TASK is None

    asyncio.run(run())


def test_application_starts_sync_only_in_enabled_production(monkeypatch):
    from app import main

    starts = []
    monkeypatch.setattr(auto_sync, "start_auto_sync_loop", lambda loop: starts.append(loop))
    for production, enabled in [(False, True), (True, False), (True, True)]:
        monkeypatch.setattr(main, "settings", SimpleNamespace(is_production=production, AUTO_SYNC_ENABLED=enabled))
        asyncio.run(main.start_provider_sync())
    assert len(starts) == 1


def test_scheduled_pass_uses_real_importer_with_date_cursor(monkeypatch):
    from app.services import imports_strava

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(auto_sync, "SessionLocal", sessions)
    with sessions() as db:
        user = User(email="cursor@example.invalid", password_hash="!disabled-test")
        db.add(user)
        db.flush()
        db.add(OAuthAccount(user_id=user.id, provider="strava", access_token="test-token", expires_at=2147483647))
        db.commit()
    cursors = []

    def pull(token, page, per_page, after_epoch):
        cursors.append(after_epoch)
        return [{"id": 123, "type": "Ride", "start_date": "2026-11-01T12:00:00Z", "elapsed_time": 600}]

    monkeypatch.setattr(imports_strava, "_pull_page", pull)
    auto_sync._sync_accounts()
    # The real importer must reach the provider read, rather than failing on a
    # string cursor before it ever requests a page.
    assert len(cursors) == 1
    assert isinstance(cursors[0], int)
    auto_sync._sync_accounts()
    with sessions() as db:
        activity = db.query(Activity).one()
        assert activity.provider == "strava"
        assert activity.created_at is not None
        assert activity.duration_s == 600
    engine.dispose()


@pytest.mark.parametrize("existing", [False, True])
def test_scheduled_sync_with_current_meals_persists_updates_and_preserves_locked_day(monkeypatch, caplog, existing):
    from datetime import UTC, datetime, timedelta

    from app.models import Plan, PlanItem, PlanMeal
    from app.services import imports_strava

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(auto_sync, "SessionLocal", sessions)
    started = datetime.now(UTC) - timedelta(minutes=1)
    today = datetime.now(UTC).date()
    with sessions() as db:
        user = User(email="current-meals@example.invalid", password_hash="!disabled-test", timezone="UTC", weight_kg=70)
        db.add(user)
        db.flush()
        db.add(OAuthAccount(user_id=user.id, provider="strava", access_token="test-token", expires_at=2147483647))
        if existing:
            db.add(
                Activity(
                    user_id=user.id,
                    provider="strava",
                    source_provider="strava",
                    source_id="123",
                    start_time=started.replace(tzinfo=None),
                    sport="cycling",
                    duration_s=600,
                    kcal=100,
                )
            )
        for offset, locked in [(0, False), (1, True)]:
            plan = Plan(user_id=user.id, date=today + timedelta(days=offset), locked=locked, source="ai")
            meal = PlanMeal(meal_type="dinner", title="Original dinner", kcal=600, carbs_g=80, protein_g=30, fat_g=18)
            meal.items = [PlanItem(name="Rice", qty=200, unit="g", kcal=260, carbs_g=56, protein_g=5, fat_g=1)]
            plan.meals = [meal]
            db.add(plan)
        db.commit()
        locked_plan = db.query(Plan).filter_by(locked=True).one()
        locked_id = locked_plan.id

        def snapshot(plan):
            rows = [plan, *plan.meals, *(item for meal in plan.meals for item in meal.items)]
            return [{c.name: getattr(row, c.name) for c in row.__table__.columns} for row in rows]

        before = snapshot(locked_plan)

    monkeypatch.setattr(
        imports_strava,
        "_pull_page",
        lambda *a, **kw: [
            {"id": 123, "type": "Ride", "start_date": started.isoformat(), "elapsed_time": 3600, "calories": 700}
        ],
    )
    with caplog.at_level(logging.INFO, logger="glycofy.auto_sync"):
        auto_sync._sync_accounts()
    assert [r.status for r in caplog.records if r.name == "glycofy.auto_sync"] == ["completed"]
    with sessions() as db:
        activity = db.query(Activity).one()
        assert activity.duration_s == 3600
        assert activity.kcal == 700
        assert activity.start_time == started.replace(tzinfo=None)
        assert snapshot(db.get(Plan, locked_id)) == before
    engine.dispose()
