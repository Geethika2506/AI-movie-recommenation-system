import json
import time
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


class RetryableTMDBError(httpx.HTTPStatusError):
    pass


class TMDBClient:
    def __init__(self, token: str | None, cache_dir: Path, ttl_seconds: int = 86_400) -> None:
        self.token = token
        self.cache_dir = cache_dir
        self.ttl_seconds = ttl_seconds
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.25, max=2),
        retry=retry_if_exception_type(
            (httpx.TimeoutException, httpx.NetworkError, RetryableTMDBError)
        ),
        reraise=True,
    )
    def _get(self, path: str, *, params: dict[str, Any] | None = None, cache_name: str | None = None) -> dict[str, Any]:
        if not self.token:
            raise RuntimeError("TMDB token is not configured")
        cache = self.cache_dir / cache_name if cache_name else None
        if cache and cache.exists() and time.time() - cache.stat().st_mtime < self.ttl_seconds:
            return json.loads(cache.read_text())
        response = httpx.get(
            f"https://api.themoviedb.org/3/{path.lstrip('/')}",
            headers={"Authorization": f"Bearer {self.token}"},
            params=params or {},
            timeout=10,
        )
        if response.status_code == 429 or response.status_code >= 500:
            retry_after = response.headers.get("retry-after")
            if retry_after and retry_after.isdigit():
                time.sleep(min(int(retry_after), 10))
            raise RetryableTMDBError(
                "retryable TMDB response",
                request=response.request,
                response=response,
            )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        if cache:
            cache.write_text(json.dumps(payload))
        return payload

    def movie_genre_map(self) -> dict[int, str]:
        payload = self._get("genre/movie/list", cache_name="genre-movie-list.json")
        return {
            int(item["id"]): str(item["name"])
            for item in payload.get("genres") or []
            if item.get("id") is not None and item.get("name")
        }

    def discover_movies(
        self,
        *,
        release_date_gte: str,
        page: int = 1,
        sort_by: str = "popularity.desc",
    ) -> dict[str, Any]:
        return self._get(
            "discover/movie",
            params={
                "include_adult": "false",
                "include_video": "false",
                "sort_by": sort_by,
                "primary_release_date.gte": release_date_gte,
                "page": page,
            },
            cache_name=f"discover-{release_date_gte}-p{page}.json",
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.25, max=2),
        retry=retry_if_exception_type(
            (httpx.TimeoutException, httpx.NetworkError, RetryableTMDBError)
        ),
        reraise=True,
    )
    def movie(self, tmdb_id: int) -> dict[str, Any] | None:
        if not self.token:
            return None
        cache = self.cache_dir / f"movie-{tmdb_id}.json"
        if cache.exists() and time.time() - cache.stat().st_mtime < self.ttl_seconds:
            return json.loads(cache.read_text())
        payload = self._get(
            f"movie/{tmdb_id}",
            params={"append_to_response": "credits,keywords,release_dates,watch/providers"},
        )
        cache.write_text(json.dumps(payload))
        return payload
