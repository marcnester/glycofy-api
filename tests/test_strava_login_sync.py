from datetime import datetime, timedelta

from fastapi import BackgroundTasks
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import Activity, OAuthAccount, User
from app.routers import oauth_strava


def _db() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _linked_user(db: Session, *, updated_at: datetime) -> User:
    user = User(email="athlete@example.com", password_hash="test")
    db.add(user)
    db.flush()
    db.add(
        OAuthAccount(
            user_id=user.id,
            provider="strava",
            access_token="token",
            refresh_token="refresh",
            expires_at=4_000_000_000,
            updated_at=updated_at,
        )
    )
    db.commit()
    db.refresh(user)
    return user


def _activity(db: Session, user: User, started_at: datetime) -> None:
    db.add(
        Activity(
            user_id=user.id,
            provider="strava",
            source_provider="strava",
            source_id="activity-1",
            sport="Cycling",
            start_time=started_at,
            duration_s=3600,
            kcal=600,
            distance_m=30_000,
            created_at=started_at,
        )
    )
    db.commit()


def test_login_sync_skips_recently_synced_linked_user(monkeypatch):
    db = _db()
    try:
        user = _linked_user(db, updated_at=datetime.utcnow())
        _activity(db, user, datetime.utcnow() - timedelta(hours=2))
        monkeypatch.setattr(oauth_strava, "_configured", lambda: True)

        tasks = BackgroundTasks()
        queued = oauth_strava.schedule_strava_sync_on_login(
            background_tasks=tasks,
            db=db,
            user_id=user.id,
            freshness_minutes=30,
        )

        assert queued is False
        assert tasks.tasks == []
    finally:
        db.close()


def test_login_sync_queues_stale_user_once(monkeypatch):
    db = _db()
    user = None
    try:
        user = _linked_user(db, updated_at=datetime.utcnow() - timedelta(hours=2))
        _activity(db, user, datetime.utcnow() - timedelta(hours=2))
        monkeypatch.setattr(oauth_strava, "_configured", lambda: True)

        tasks = BackgroundTasks()
        first = oauth_strava.schedule_strava_sync_on_login(
            background_tasks=tasks,
            db=db,
            user_id=user.id,
            freshness_minutes=30,
        )
        second = oauth_strava.schedule_strava_sync_on_login(
            background_tasks=tasks,
            db=db,
            user_id=user.id,
            freshness_minutes=30,
        )

        assert first is True
        assert second is False
        assert len(tasks.tasks) == 1
    finally:
        if user is not None:
            with oauth_strava._LOGIN_SYNC_LOCK:
                oauth_strava._LOGIN_SYNCS_IN_FLIGHT.discard(user.id)
        db.close()


def test_incremental_sync_uses_latest_activity_with_overlap(monkeypatch):
    db = _db()
    try:
        user = _linked_user(db, updated_at=datetime.utcnow() - timedelta(hours=2))
        latest = datetime(2026, 8, 4, 18, 0, 0)
        _activity(db, user, latest)
        monkeypatch.setattr(oauth_strava, "_configured", lambda: True)

        captured = {}

        def fake_fetch(_token, after_ts=None):
            captured["after_ts"] = after_ts
            return []

        monkeypatch.setattr(oauth_strava, "_fetch_all_strava_activities", fake_fetch)
        result = oauth_strava.sync_strava(replace=False, months=6, db=db, user=user)

        expected = int(
            (latest - timedelta(hours=oauth_strava.INCREMENTAL_OVERLAP_HOURS))
            .replace(tzinfo=oauth_strava.UTC)
            .timestamp()
        )
        assert captured["after_ts"] == expected
        assert result["fetched"] == 0
    finally:
        db.close()


def test_scheduled_sync_preserves_and_recovers_strength_calories(monkeypatch):
    from app.services import imports_strava

    with _db() as db:
        user = _linked_user(db, updated_at=datetime.utcnow())
        for source_id, kcal in [("9", 716), ("6", None)]:
            db.add(
                Activity(
                    user_id=user.id,
                    provider="strava",
                    source_provider="strava",
                    source_id=source_id,
                    sport="WeightTraining",
                    start_time=datetime(2026, 11, int(source_id)),
                    kcal=kcal,
                )
            )
        db.commit()

        feed = [
            {
                "id": int(source_id),
                "type": "WeightTraining",
                "start_date": f"2026-11-0{source_id}T18:00:00Z",
                "elapsed_time": 2700,
            }
            for source_id in ("9", "6")
        ]
        monkeypatch.setattr(imports_strava, "_pull_page", lambda *args, **kwargs: feed)
        fetched = []

        def detail(_token, source_id):
            fetched.append(source_id)
            return 310

        monkeypatch.setattr(imports_strava, "fetch_activity_calories", detail)
        imports_strava.sync_strava(db, user)
        imports_strava.sync_strava(db, user)

        assert fetched == ["6"]
        assert {row.source_id: row.kcal for row in db.query(Activity).all()} == {"9": 716, "6": 310}
