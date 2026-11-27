"""Per-client fixed-window rate limit (requests per minute), shared across instances via Redis.

Protects the free LLM/speech quotas from one noisy client. Fails open if the cache is down.
"""
from __future__ import annotations

import time
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from backend.core.cache import get_cache
from backend.core.config import get_settings

LIMITED_PATHS = ("/chat", "/voice", "/eligibility", "/search")


def client_id(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, per_minute: Optional[int] = None) -> None:
        super().__init__(app)
        self.per_minute = per_minute if per_minute is not None else get_settings().rate_limit_per_min

    async def dispatch(self, request: Request, call_next):
        if self.per_minute <= 0 or request.method != "POST" or not request.url.path.startswith(LIMITED_PATHS):
            return await call_next(request)
        window = int(time.time() // 60)
        key = f"haqdar:rl:{client_id(request)}:{window}"
        count = get_cache().incr(key, ttl=70)
        if count > self.per_minute:
            retry = 60 - int(time.time() % 60)
            return JSONResponse({"detail": "Too many requests. Please wait a minute and try again."},
                                status_code=429, headers={"Retry-After": str(retry)})
        return await call_next(request)
