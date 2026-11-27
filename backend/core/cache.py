"""Answer cache: Redis (Upstash) when REDIS_URL is set, an in-process TTL dict otherwise.

Repeated questions ("PM-Kisan eligibility") are served without an LLM call. The key covers the
normalised question, language, retrieval mode and the eligibility profile, so two people with
different profiles never share an answer.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any, Dict, Optional, Tuple

from backend.core.config import get_settings


def cache_key(question: str, lang: str, mode: str, profile: Optional[Dict[str, Any]] = None) -> str:
    norm = " ".join((question or "").lower().split())
    prof = json.dumps({k: v for k, v in (profile or {}).items() if v is not None}, sort_keys=True,
                      ensure_ascii=False)
    digest = hashlib.sha256(f"{norm}|{lang}|{mode}|{prof}".encode("utf-8")).hexdigest()[:32]
    return f"haqdar:answer:v1:{digest}"


class Cache:
    def get(self, key: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def set(self, key: str, value: Dict[str, Any], ttl: int) -> None:
        raise NotImplementedError

    def incr(self, key: str, ttl: int) -> int:
        """Atomic counter with expiry (used by the rate limiter)."""
        raise NotImplementedError


class MemoryCache(Cache):
    def __init__(self, max_items: int = 2000) -> None:
        self._data: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self.max_items = max_items

    def _alive(self, key: str) -> Optional[Any]:
        item = self._data.get(key)
        if not item:
            return None
        if item[0] < time.time():
            self._data.pop(key, None)
            return None
        return item[1]

    def get(self, key):
        with self._lock:
            return self._alive(key)

    def set(self, key, value, ttl):
        with self._lock:
            if len(self._data) >= self.max_items:
                oldest = min(self._data, key=lambda k: self._data[k][0])
                self._data.pop(oldest, None)
            self._data[key] = (time.time() + ttl, value)

    def incr(self, key, ttl):
        with self._lock:
            cur = self._alive(key) or 0
            exp = self._data[key][0] if key in self._data else time.time() + ttl
            self._data[key] = (exp, cur + 1)
            return cur + 1


class RedisCache(Cache):
    def __init__(self, url: str) -> None:
        import redis
        self.r = redis.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2, decode_responses=True)

    def get(self, key):
        try:
            raw = self.r.get(key)
            return json.loads(raw) if raw else None
        except Exception:  # noqa: BLE001 - a cache outage must never fail a query
            return None

    def set(self, key, value, ttl):
        try:
            self.r.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)
        except Exception:  # noqa: BLE001
            pass

    def incr(self, key, ttl):
        try:
            pipe = self.r.pipeline()
            pipe.set(key, 0, ex=ttl, nx=True)  # start the window with an expiry (works on any Redis / Upstash)
            pipe.incr(key)
            return int(pipe.execute()[1])
        except Exception:  # noqa: BLE001 - fail open
            return 0


_cache: Optional[Cache] = None


def get_cache() -> Cache:
    global _cache
    if _cache is None:
        url = get_settings().redis_url
        _cache = RedisCache(url) if url else MemoryCache()
    return _cache


def set_cache(c: Optional[Cache]) -> None:
    global _cache
    _cache = c
