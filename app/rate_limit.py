from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status


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
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many requests. Please try again later.",
                    headers={"Retry-After": str(window_seconds)},
                )
            events.append(now)


AUTH_LIMITER = FixedWindowLimiter()


def client_key(request: Request) -> str:
    # Do not trust X-Forwarded-For unless a known reverse proxy normalizes it.
    return request.client.host if request.client else "unknown"
