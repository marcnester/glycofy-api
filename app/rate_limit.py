from __future__ import annotations

import hashlib
import hmac
import ipaddress
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


def client_address(request: Request) -> str:
    """Return only an address asserted by the configured edge boundary."""
    if settings.is_production and settings.TRUSTED_EDGE_PROVIDER.strip().lower() == "cloudflare":
        candidate = (request.headers.get("cf-connecting-ip") or "").strip()
        try:
            return ipaddress.ip_address(candidate).compressed
        except ValueError:
            # Never fall through to X-Forwarded-For in production. Uvicorn is
            # configured not to rewrite request.client from public headers.
            return "unverified-edge"
    return request.client.host if request.client else "unknown"


def client_key(request: Request) -> str:
    return client_address(request)


def account_key(identifier: str) -> str:
    """Return a privacy-safe, stable limiter key for an account identifier."""
    normalized = identifier.strip().casefold()
    return hmac.new(settings.JWT_SECRET.encode(), normalized.encode(), hashlib.sha256).hexdigest()
