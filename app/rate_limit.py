from __future__ import annotations

import hashlib
import hmac
import ipaddress
import threading
import time
from collections import defaultdict, deque
from typing import cast

from fastapi import HTTPException, Request, status
from redis import Redis  # type: ignore[import-untyped]
from redis.exceptions import RedisError  # type: ignore[import-untyped]

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


class DistributedLimiter:
    """Use Redis atomically when configured; retain the single-process backend for local/one-process deployments."""

    _SCRIPT = """
    local current = redis.call('INCR', KEYS[1])
    if current == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
    return {current, redis.call('TTL', KEYS[1])}
    """

    def __init__(self) -> None:
        self._local = FixedWindowLimiter()
        self._client: Redis | None = None
        self._client_url: str | None = None

    def _redis(self) -> Redis | None:
        url = settings.SHARED_RATE_LIMIT_URL
        if not url:
            return None
        if self._client is None or self._client_url != url:
            self._client = Redis.from_url(url, decode_responses=False, socket_connect_timeout=2, socket_timeout=2)
            self._client_url = url
        return self._client

    def check(self, key: str, *, maximum: int, window_seconds: int) -> None:
        client = self._redis()
        if client is None:
            self._local.check(key, maximum=maximum, window_seconds=window_seconds)
            return
        redis_key = f"glycofy:rate:{key}"
        try:
            result = cast(list[int], client.eval(self._SCRIPT, 1, redis_key, str(window_seconds)))
            current, ttl = result
        except RedisError as exc:
            if settings.is_production:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Authentication protection is temporarily unavailable.",
                ) from exc
            self._local.check(key, maximum=maximum, window_seconds=window_seconds)
            return
        if int(current) > maximum:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please try again later.",
                headers={"Retry-After": str(max(1, int(ttl)))},
            )

    def clear(self) -> None:
        self._local.clear()


AUTH_LIMITER = DistributedLimiter()


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
