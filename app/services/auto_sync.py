"""Periodic read-only Strava imports, with database/provider work off the event loop."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import random
import threading

from app.db import SessionLocal
from app.models import OAuthAccount, User
from app.services.imports_strava import sync_strava

_LOG = logging.getLogger("glycofy.auto_sync")
_SYNC_LOCK = threading.Lock()
_STOP_EVENT: asyncio.Event | None = None
_TASK: asyncio.Task | None = None


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    return raw in ("1", "true", "yes", "y", "on") if raw else default


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _month_start_utc_iso(today_utc: dt.date | None = None) -> str:
    today_utc = today_utc or dt.datetime.now(dt.UTC).date()
    return today_utc.replace(day=1).isoformat()


def _sync_accounts() -> None:
    # The lock lives in the worker, so cancellation of its async waiter cannot
    # allow another pass to overlap a still-running provider request.
    if not _SYNC_LOCK.acquire(blocking=False):
        return
    try:
        with SessionLocal() as db:
            account_ids = [row.id for row in db.query(OAuthAccount.id).filter(OAuthAccount.provider == "strava").all()]
        for account_id in account_ids:
            # Each account has its own transaction/session; provider or DB errors
            # cannot leave the next account's transaction in a failed state.
            try:
                with SessionLocal() as db:
                    account = db.get(OAuthAccount, account_id)
                    if account is None or not (account.access_token or "").strip():
                        continue
                    user = db.get(User, account.user_id)
                    if user is None:
                        continue
                    result = sync_strava(db, user, _month_start_utc_iso())
                    if result.get("error") or not result.get("linked"):
                        _LOG.warning("strava_auto_sync", extra={"status": "provider_unavailable"})
                    else:
                        _LOG.info("strava_auto_sync", extra={"status": "completed"})
            except Exception:
                # Provider exceptions may include URLs or credentials. Never log
                # exception text, token values, or activity/profile data here.
                _LOG.error("strava_auto_sync", extra={"status": "failed"})
    finally:
        _SYNC_LOCK.release()


async def _sync_once(stop_event: asyncio.Event | None = None) -> None:
    jitter = max(_int_env("AUTO_SYNC_JITTER_SECS", 120), 0)
    if jitter:
        delay = random.uniform(0, float(jitter))
        if stop_event is None:
            await asyncio.sleep(delay)
        elif await _wait_for_stop(stop_event, delay):
            return
    await asyncio.to_thread(_sync_accounts)


async def _wait_for_stop(stop_event: asyncio.Event, seconds: float) -> bool:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
        return True
    except TimeoutError:
        return False


async def _loop(stop_event: asyncio.Event) -> None:
    interval = max(_int_env("AUTO_SYNC_INTERVAL_HOURS", 24), 1) * 3600
    if await _wait_for_stop(stop_event, 5):
        return
    while not stop_event.is_set():
        if _bool_env("AUTO_SYNC_ENABLED", True):
            try:
                await _sync_once(stop_event)
            except Exception:
                _LOG.error("strava_auto_sync", extra={"status": "pass_failed"})
        if await _wait_for_stop(stop_event, interval):
            return


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
            # Keep the task/lock alive if a bounded provider request is still
            # finishing. Cancelling a to_thread waiter does not stop its thread.
            await asyncio.wait_for(asyncio.shield(_TASK), timeout=10)
        except TimeoutError:
            _LOG.warning("strava_auto_sync", extra={"status": "shutdown_pending"})
            return
        _TASK = None
        _STOP_EVENT = None
