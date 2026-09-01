from __future__ import annotations

import hashlib
import hmac
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

from app.config import settings


class FixedWindowLimiter:
    """Small single-process limiter; production should use a shared Redis backend."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, *, maximum: int, window_seconds: int) -> None:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= maximum:
                retry_after = max(1, int(window_seconds - (now - events[0])))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many requests. Please try again later.",
                    headers={"Retry-After": str(retry_after)},
                )
            events.append(now)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


AUTH_LIMITER = FixedWindowLimiter()


def client_key(request: Request) -> str:
    # Do not trust X-Forwarded-For unless a known reverse proxy normalizes it.
    return request.client.host if request.client else "unknown"


def account_key(identifier: str) -> str:
    """Return a privacy-safe, stable limiter key for an account identifier."""
    normalized = identifier.strip().casefold()
    return hmac.new(settings.JWT_SECRET.encode(), normalized.encode(), hashlib.sha256).hexdigest()
