import json
import logging
import threading
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.models import Movie

logger = logging.getLogger("cinematch")
USER_AGENT = "CineMatch/0.1 (local movie recommender; educational use)"
CACHE_VERSION = 2
WIKIPEDIA_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
IMDB_SUGGESTION = "https://v3.sg.media-imdb.com/suggestion/t/{imdb_id}.json"
_cache_lock = threading.Lock()


def wikipedia_cache_path(data_dir: Path) -> Path:
    return data_dir / "processed" / "latest-small" / "wikipedia.jsonl"


def load_poster_cache(path: Path) -> dict[int, str | None]:
    cache: dict[int, str | None] = {}
    if not path.exists():
        return cache
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        movie_id = int(item["movie_id"])
        poster_path = item.get("poster_path")
        version = int(item.get("v") or 1)
        if poster_path:
            cache[movie_id] = poster_path
        elif version >= CACHE_VERSION:
            cache[movie_id] = None
        else:
            cache.pop(movie_id, None)
    return cache


def append_poster_cache(path: Path, movie_id: int, poster_path: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"movie_id": movie_id, "poster_path": poster_path, "v": CACHE_VERSION})
    with _cache_lock, path.open("a") as stream:
        stream.write(payload + "\n")


@lru_cache(maxsize=8)
def load_imdb_ids(data_dir: str) -> dict[int, int]:
    path = Path(data_dir) / "processed" / "latest-small" / "links.csv"
    if not path.exists():
        return {}
    frame = pd.read_csv(path)
    ids: dict[int, int] = {}
    for row in frame.itertuples(index=False):
        if pd.isna(row.imdb_id):
            continue
        ids[int(row.movie_id)] = int(row.imdb_id)
    return ids


def format_imdb_id(imdb_id: int) -> str:
    return f"tt{int(imdb_id):07d}"


def natural_title(title: str) -> str:
    for article in ("The", "A", "An"):
        suffix = f", {article}"
        if title.endswith(suffix):
            return f"{article} {title[: -len(suffix)]}"
    return title


def candidate_titles(title: str, year: int | None) -> list[str]:
    names: list[str] = []
    cleaned = natural_title(title).strip().strip("'\"")
    for form in dict.fromkeys([cleaned, natural_title(title), title]):
        if year:
            names.extend([f"{form} ({year} film)", f"{form} ({year})"])
        names.append(form)
    return names


def clean_image_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _get(client: httpx.Client, url: str, **kwargs: object) -> httpx.Response:
    last: httpx.Response | None = None
    for attempt in range(3):
        last = client.get(url, **kwargs)  # type: ignore[arg-type]
        if last.status_code != 429:
            return last
        wait = last.headers.get("retry-after")
        numeric = bool(wait and str(wait).replace(".", "", 1).isdigit())
        delay = float(wait) if numeric else 0.4 * (attempt + 1)
        time.sleep(min(delay, 4))
    assert last is not None
    return last


def summary_image(title: str, client: httpx.Client) -> str | None:
    encoded = quote(title.replace(" ", "_"), safe="()_,:'/")
    try:
        response = _get(client, WIKIPEDIA_SUMMARY.format(title=encoded), follow_redirects=True)
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    payload = response.json()
    image = payload.get("originalimage") or payload.get("thumbnail") or {}
    source = image.get("source")
    return clean_image_url(str(source)) if source else None


def poster_from_wikipedia(title: str, year: int | None, client: httpx.Client) -> str | None:
    for candidate in candidate_titles(title, year):
        image = summary_image(candidate, client)
        if image:
            return image
    return None


def _search_score(hit_title: str, title: str, year: int | None) -> int:
    lowered = hit_title.lower()
    score = 0
    if "disambiguation" in lowered or "soundtrack" in lowered:
        return -10
    target = natural_title(title).strip("'\"").lower()
    if target and target in lowered:
        score += 4
    if "(film)" in lowered or "(miniseries)" in lowered:
        score += 3
    if year and str(year) in lowered:
        score += 3
    return score


def poster_from_wikipedia_search(title: str, year: int | None, client: httpx.Client) -> str | None:
    parts = [natural_title(title).strip("'\""), str(year) if year else "", "film"]
    query = " ".join(part for part in parts if part)
    try:
        response = _get(
            client,
            WIKIPEDIA_API,
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": 5,
                "format": "json",
            },
        )
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    hits = (response.json().get("query") or {}).get("search") or []
    ranked = sorted(
        hits,
        key=lambda hit: _search_score(str(hit.get("title") or ""), title, year),
        reverse=True,
    )
    for hit in ranked:
        hit_title = str(hit.get("title") or "")
        if _search_score(hit_title, title, year) < 1:
            continue
        image = summary_image(hit_title, client)
        if image:
            return image
    return None


def poster_from_imdb(imdb_id: int, client: httpx.Client) -> str | None:
    formatted = format_imdb_id(imdb_id)
    try:
        response = _get(client, IMDB_SUGGESTION.format(imdb_id=formatted))
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    for item in response.json().get("d") or []:
        if item.get("id") != formatted:
            continue
        image = (item.get("i") or {}).get("imageUrl")
        if image:
            return clean_image_url(str(image))
    return None


def lookup_poster(
    title: str, year: int | None, imdb_id: int | None, client: httpx.Client
) -> str | None:
    if imdb_id:
        image = poster_from_imdb(imdb_id, client)
        if image:
            return image
    return poster_from_wikipedia(title, year, client) or poster_from_wikipedia_search(
        title, year, client
    )


def hydrate_posters(
    session: Session,
    movies: Sequence[Movie],
    data_dir: Path,
    *,
    enabled: bool,
    client: httpx.Client | None = None,
) -> None:
    """Fill missing poster URLs for the movies about to be shown."""
    if not movies:
        return
    cache_path = wikipedia_cache_path(data_dir)
    cache = load_poster_cache(cache_path)
    imdb_ids = load_imdb_ids(str(data_dir.resolve()))
    changed = False
    pending: list[Movie] = []
    for movie in movies:
        if movie.poster_path:
            continue
        cached = cache.get(movie.id)
        if cached:
            movie.poster_path = cached
            changed = True
        elif movie.id not in cache and enabled:
            pending.append(movie)
    if pending:

        def lookup(movie: Movie, http: httpx.Client) -> tuple[Movie, str | None]:
            return movie, lookup_poster(movie.title, movie.year, imdb_ids.get(movie.id), http)

        if client is not None:
            found = [lookup(movie, client) for movie in pending]
        else:

            def fetch(movie: Movie) -> tuple[Movie, str | None]:
                with httpx.Client(
                    timeout=8, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
                ) as http:
                    return lookup(movie, http)

            with ThreadPoolExecutor(max_workers=min(6, len(pending))) as pool:
                found = list(pool.map(fetch, pending))
        for movie, poster in found:
            append_poster_cache(cache_path, movie.id, poster)
            if poster:
                movie.poster_path = poster
                changed = True
    if changed:
        session.commit()
        for movie in movies:
            session.refresh(movie)


def enrich_wikipedia_posters(
    factory: sessionmaker[Session],
    data_dir: Path,
    limit: int | None = None,
    client: httpx.Client | None = None,
) -> int:
    """Look up posters for movies that still lack artwork."""
    cache_path = wikipedia_cache_path(data_dir)
    cache = load_poster_cache(cache_path)
    imdb_ids = load_imdb_ids(str(data_dir.resolve()))
    written = 0
    own_client = client is None
    http = client or httpx.Client(
        timeout=8, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with factory() as session:
            movies = list(session.scalars(select(Movie).order_by(Movie.popularity_score.desc())))
            looked_up = 0
            for movie in movies:
                if movie.poster_path:
                    continue
                if movie.id in cache:
                    cached = cache[movie.id]
                    if cached:
                        movie.poster_path = cached
                        written += 1
                    continue
                poster = lookup_poster(movie.title, movie.year, imdb_ids.get(movie.id), http)
                append_poster_cache(cache_path, movie.id, poster)
                cache[movie.id] = poster
                looked_up += 1
                if poster:
                    movie.poster_path = poster
                    written += 1
                if limit is not None and looked_up >= limit:
                    break
            session.commit()
    finally:
        if own_client:
            http.close()
    logger.info("posters_enriched", extra={"updated": written})
    return written


def apply_wikipedia_cache_to_metadata(metadata: dict[int, dict[str, object]], path: Path) -> None:
    for movie_id, poster_path in load_poster_cache(path).items():
        if not poster_path:
            continue
        details = metadata.setdefault(movie_id, {})
        details.setdefault("poster_path", poster_path)
