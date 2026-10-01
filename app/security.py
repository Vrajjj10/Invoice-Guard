"""Demo protection: X-API-Key check and a per-IP upload rate limit (in-memory, single process)."""

import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Request

from app.config import get_settings


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    settings = get_settings()
    if not settings.demo_api_key:
        if settings.environment == "dev":
            return  # local development: auth off
        raise HTTPException(503, "DEMO_API_KEY is not configured on the server")
    if x_api_key is None or not secrets.compare_digest(x_api_key, settings.demo_api_key):
        raise HTTPException(401, "Missing or invalid X-API-Key")


class RateLimiter:
    """Sliding window: at most `limit` hits per `window` seconds per client key."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window: float) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= window:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


upload_limiter = RateLimiter()


def client_ip(request: Request) -> str:
    # Render sits behind a proxy: the real client is the first X-Forwarded-For entry
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def limit_uploads(request: Request) -> None:
    settings = get_settings()
    if not upload_limiter.check(
        client_ip(request), settings.upload_rate_limit, settings.upload_rate_window
    ):
        raise HTTPException(429, "Too many uploads; slow down", headers={"Retry-After": "60"})


