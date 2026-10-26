"""Small bounded recommendation cache with optional Redis sharing."""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from contextlib import suppress
from threading import Lock

from redis import Redis
from redis.exceptions import RedisError

CachedRows = list[tuple[int, float, str]]


class RecommendationCache:
    def __init__(
        self,
        ttl_seconds: int = 90,
        max_entries: int = 256,
        redis_url: str | None = None,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._entries: OrderedDict[str, tuple[float, CachedRows]] = OrderedDict()
        self._lock = Lock()
        self._redis = Redis.from_url(redis_url, decode_responses=True) if redis_url else None

    def get(self, key: str) -> CachedRows | None:
        redis_key = f"cinematch:recommendations:{key}"
        if self._redis:
            try:
                value = self._redis.get(redis_key)
                if value:
                    return [
                        (int(movie_id), float(score), str(reason))
                        for movie_id, score, reason in json.loads(value)
                    ]
            except (RedisError, TypeError, ValueError, json.JSONDecodeError):
                pass
        now = time.monotonic()
        with self._lock:
            hit = self._entries.get(key)
            if not hit:
                return None
            expires_at, rows = hit
            if expires_at <= now:
                self._entries.pop(key, None)
                return None
            self._entries.move_to_end(key)
            return list(rows)

    def set(self, key: str, rows: CachedRows) -> None:
        stored = list(rows)
        if self._redis:
            with suppress(RedisError):
                self._redis.setex(
                    f"cinematch:recommendations:{key}",
                    self.ttl_seconds,
                    json.dumps(stored, separators=(",", ":")),
                )
        with self._lock:
            self._entries[key] = (time.monotonic() + self.ttl_seconds, stored)
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def close(self) -> None:
        if self._redis:
            self._redis.close()
