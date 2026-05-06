"""Per-user rate limiting middleware using token bucket algorithm."""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from threading import Lock

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse


@dataclass
class TokenBucket:
    capacity: int
    refill_rate: float
    tokens: float = -1.0
    last_refill: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        if self.tokens < 0:
            self.tokens = float(self.capacity)

    def consume(self) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class RateLimitConfig:
    def __init__(
        self,
        default_rpm: int = 100,
        chat_rpm: int = 30,
        health_rpm: int = 0,
    ) -> None:
        self.default_rpm = default_rpm
        self.chat_rpm = chat_rpm
        self.health_rpm = health_rpm


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, config: RateLimitConfig | None = None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self._config = config or RateLimitConfig()
        self._buckets: dict[str, TokenBucket] = defaultdict(
            lambda: TokenBucket(capacity=self._config.default_rpm, refill_rate=self._config.default_rpm / 60.0)
        )
        self._chat_buckets: dict[str, TokenBucket] = defaultdict(
            lambda: TokenBucket(capacity=self._config.chat_rpm, refill_rate=self._config.chat_rpm / 60.0)
        )
        self._lock = Lock()

    def _get_user_key(self, request: Request) -> str:
        email = request.headers.get("X-Forwarded-Email", "")
        if email:
            return email
        return request.client.host if request.client else "unknown"

    def _is_chat_path(self, path: str) -> bool:
        return "/chat/" in path

    def _is_health_path(self, path: str) -> bool:
        return "/health/" in path

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        if self._is_health_path(path):
            return await call_next(request)

        user_key = self._get_user_key(request)

        with self._lock:
            if self._is_chat_path(path):
                bucket = self._chat_buckets[user_key]
            else:
                bucket = self._buckets[user_key]

            allowed = bucket.consume()

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Please try again shortly."},
                headers={"Retry-After": "10"},
            )

        return await call_next(request)
