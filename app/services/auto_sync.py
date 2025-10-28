# app/services/auto_sync.py
"""
Background auto-sync for Strava using asyncio (no new deps).

- Scans linked Strava accounts once per interval (default 24h)
- For each linked user, calls imports_strava.sync_strava(...)
- Uses month-start as the 'since' cursor (safe & idempotent)
- Guard against overlapping runs
- Controlled by env:
    AUTO_SYNC_ENABLED=true|false   (default true)
    AUTO_SYNC_INTERVAL_HOURS=24    (int)
    AUTO_SYNC_JITTER_SECS=120      (small random delay to stagger)
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import random

from app.db import SessionLocal
from app.models import OAuthAccount, User
from app.services.imports_strava import sync_strava

_RUNNING = False
_STOP_EVENT: asyncio.Event | None = None
_TASK: asyncio.Task | None = None


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "y", "on")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except Exception:
        return default


def _month_start_utc_iso(today_utc: dt.date | None = None) -> str:
    if today_utc is None:
        today_utc = dt.datetime.utcnow().date()
    first = today_utc.replace(day=1)
    return first.isoformat()


async def _sync_once() -> None:
    """One full sync pass over all linked Strava accounts."""
    global _RUNNING
    if _RUNNING:
        return

    _RUNNING = True
    try:
        jitter = _int_env("AUTO_SYNC_JITTER_SECS", 120)
        if jitter > 0:
            await asyncio.sleep(random.uniform(0, float(jitter)))

        since_iso = _month_start_utc_iso()
        db = SessionLocal()
        try:
            linked: list[OAuthAccount] = (
                db.query(OAuthAccount).filter(OAuthAccount.provider == "strava", OAuthAccount.linked == True).all()
            )

            print(f"🌀 [auto-sync] linked={len(linked)} since={since_iso}")

            for oa in linked:
                try:
                    user: User | None = db.query(User).filter(User.id == oa.user_id).first()
                    if not user:
                        print(f"⚠️  [auto-sync] skip oa_id={oa.id}: user not found")
                        continue

                    res = sync_strava(db, user, since_iso)
                    created = res.get("created", 0)
                    updated = res.get("updated", 0)
                    skipped = res.get("skipped", 0)
                    print(f"✅ [auto-sync] user={user.id} created={created} updated={updated} skipped={skipped}")
                except Exception as e:
                    print(f"❌ [auto-sync] user_id={oa.user_id}: {e}")
                    try:
                        db.rollback()
                    except Exception:
                        pass
        finally:
            try:
                db.close()
            except Exception:
                pass
    finally:
        _RUNNING = False


async def _loop(stop_event: asyncio.Event) -> None:
    interval_hrs = _int_env("AUTO_SYNC_INTERVAL_HOURS", 24)
    interval_hrs = max(interval_hrs, 1)
    interval = interval_hrs * 3600

    print(f"⏱️  [auto-sync] loop started (interval={interval_hrs}h, enabled={_bool_env('AUTO_SYNC_ENABLED', True)})")

    try:
        await asyncio.sleep(5)
        if not stop_event.is_set() and _bool_env("AUTO_SYNC_ENABLED", True):
            await _sync_once()
    except Exception as e:
        print(f"❌ [auto-sync] initial pass failed: {e}")

    while not stop_event.is_set():
        slept = 0
        while slept < interval and not stop_event.is_set():
            step = min(30, interval - slept)
            await asyncio.sleep(step)
            slept += step

        if stop_event.is_set():
            break

        if _bool_env("AUTO_SYNC_ENABLED", True):
            try:
                await _sync_once()
            except Exception as e:
                print(f"❌ [auto-sync] pass failed: {e}")

    print("🛑 [auto-sync] loop stopped")


def start_auto_sync_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _STOP_EVENT, _TASK
    if _TASK and not _TASK.done():
        return
    _STOP_EVENT = asyncio.Event()
    _TASK = loop.create_task(_loop(_STOP_EVENT))


async def stop_auto_sync_loop() -> None:
    global _STOP_EVENT, _TASK
    if _STOP_EVENT is not None:
        _STOP_EVENT.set()
    if _TASK is not None:
        try:
            await asyncio.wait_for(_TASK, timeout=10)
        except Exception:
            pass
        _TASK = None
        _STOP_EVENT = None
