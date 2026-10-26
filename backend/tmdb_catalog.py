"""Import recent TMDB discover results into the application movie catalog."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models import Movie
from backend.tmdb import TMDBClient

# Align TMDB genre labels with MovieLens-style tags used elsewhere in the app.
GENRE_ALIASES: dict[str, str] = {
    "Science Fiction": "Sci-Fi",
    "Family": "Children's",
}


def normalize_tmdb_genres(names: list[str]) -> list[str]:
    mapped: list[str] = []
    for name in names:
        label = GENRE_ALIASES.get(name, name)
        if label and label not in mapped:
            mapped.append(label)
    return mapped


def _year_from_release(release_date: str | None) -> int | None:
    if not release_date or len(release_date) < 4:
        return None
    try:
        return int(release_date[:4])
    except ValueError:
        return None


def import_discover_movies(
    session: Session,
    client: TMDBClient,
    *,
    years: int = 3,
    limit: int = 500,
) -> tuple[int, int]:
    """Upsert popular recent TMDB titles. Returns (inserted, updated) counts."""
    if not client.enabled:
        raise ValueError("CINEMATCH_TMDB_TOKEN is required for TMDB discover import")
    if years < 1:
        raise ValueError("years must be at least 1")
    if limit < 1:
        raise ValueError("limit must be at least 1")

    genre_map = client.movie_genre_map()
    today = date.today()
    release_gte = date(today.year - years, today.month, min(today.day, 28)).isoformat()

    inserted = 0
    updated = 0
    seen_tmdb: set[int] = set()
    page = 1
    total_pages = 1

    while len(seen_tmdb) < limit and page <= total_pages:
        payload = client.discover_movies(release_date_gte=release_gte, page=page)
        total_pages = int(payload.get("total_pages") or 1)
        for row in payload.get("results") or []:
            if len(seen_tmdb) >= limit:
                break
            tmdb_id = int(row["id"])
            if tmdb_id in seen_tmdb:
                continue
            seen_tmdb.add(tmdb_id)
            genre_names = [
                genre_map[genre_id]
                for genre_id in row.get("genre_ids") or []
                if genre_id in genre_map
            ]
            genres = normalize_tmdb_genres(genre_names)
            title = str(row.get("title") or row.get("name") or "").strip()
            if not title:
                continue
            release_date = row.get("release_date")
            year = _year_from_release(release_date)
            popularity = float(row.get("popularity") or 0.0)
            vote_average = row.get("vote_average")
            overview = row.get("overview") or None
            poster_path = row.get("poster_path") or None

            existing = session.scalar(select(Movie).where(Movie.tmdb_id == tmdb_id))
            if existing:
                existing.title = title
                existing.year = year
                existing.genres = genres
                existing.overview = overview
                existing.poster_path = poster_path
                existing.vote_average = float(vote_average) if vote_average is not None else None
                existing.popularity_score = max(existing.popularity_score, popularity / 100.0)
                existing.updated_at = datetime.now(UTC)
                updated += 1
                continue

            next_id = (session.scalar(select(func.max(Movie.id))) or 0) + 1
            session.add(
                Movie(
                    id=next_id,
                    title=title,
                    year=year,
                    genres=genres,
                    tmdb_id=tmdb_id,
                    overview=overview,
                    poster_path=poster_path,
                    vote_average=float(vote_average) if vote_average is not None else None,
                    popularity_score=popularity / 100.0,
                    language=row.get("original_language"),
                )
            )
            inserted += 1
        page += 1

    session.commit()
    return inserted, updated
