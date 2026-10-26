import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.models import Movie, StreamingAvailability
from backend.posters import apply_wikipedia_cache_to_metadata, wikipedia_cache_path

YEAR_PATTERN = re.compile(r"\s+\((\d{4})\)$")


def import_prepared_catalog(factory: sessionmaker[Session], data_dir: Path) -> int:
    """Load or refresh a prepared MovieLens catalogue while preserving MovieLens IDs."""
    root = data_dir / "processed" / "latest-small"
    movies_path = root / "movies.csv"
    links_path = root / "links.csv"
    if not movies_path.exists():
        return 0

    metadata_path = root / "tmdb.jsonl"
    metadata: dict[int, dict[str, object]] = {}
    if metadata_path.exists():
        for line in metadata_path.read_text().splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            metadata[int(item["movie_id"])] = item
    apply_wikipedia_cache_to_metadata(metadata, wikipedia_cache_path(data_dir))

    links: dict[int, int | None] = {}
    if links_path.exists():
        frame = pd.read_csv(links_path)
        links = {
            int(row.movie_id): None if pd.isna(row.tmdb_id) else int(row.tmdb_id)
            for row in frame.itertuples(index=False)
        }
    popularity: dict[int, float] = {}
    ratings_path = root / "ratings.csv"
    if ratings_path.exists():
        ratings = pd.read_csv(ratings_path, usecols=["movie_id", "rating"])
        global_mean = float(ratings["rating"].mean())
        grouped = ratings.groupby("movie_id")["rating"].agg(["sum", "count"])
        popularity = {
            int(movie_id): float((row["sum"] + global_mean * 20) / (row["count"] + 20))
            for movie_id, row in grouped.iterrows()
        }

    with factory() as session:
        existing_count = session.scalar(select(func.count()).select_from(Movie)) or 0
        if existing_count:
            updated = 0
            for movie in session.scalars(select(Movie)):
                details = metadata.get(movie.id)
                mapped_tmdb_id = links.get(movie.id)
                changed = False
                if mapped_tmdb_id is not None and movie.tmdb_id != mapped_tmdb_id:
                    movie.tmdb_id = mapped_tmdb_id
                    changed = True
                mapped_popularity = popularity.get(movie.id)
                if (
                    mapped_popularity is not None
                    and abs(movie.popularity_score - mapped_popularity) > 1e-9
                ):
                    movie.popularity_score = mapped_popularity
                    changed = True
                if details:
                    fields = {
                        "overview": details.get("overview"),
                        "poster_path": details.get("poster_path"),
                        "runtime": details.get("runtime"),
                        "language": details.get("language"),
                        "cast": details.get("cast", []),
                        "director": details.get("director"),
                        "keywords": details.get("keywords", []),
                        "vote_average": details.get("vote_average"),
                    }
                    for field, value in fields.items():
                        if getattr(movie, field) != value:
                            setattr(movie, field, value)
                            changed = True
                    _replace_availability(session, movie.id, details)
                updated += int(changed)
            session.commit()
            return updated

        movies = pd.read_csv(movies_path)
        records = []
        for row in movies.itertuples(index=False):
            title = str(row.title)
            match = YEAR_PATTERN.search(title)
            details = metadata.get(int(row.movie_id), {})
            records.append(
                Movie(
                    id=int(row.movie_id),
                    title=title[: match.start()] if match else title,
                    year=int(match.group(1)) if match else None,
                    genres=[] if row.genres == "(no genres listed)" else str(row.genres).split("|"),
                    tmdb_id=links.get(int(row.movie_id)),
                    overview=details.get("overview"),
                    poster_path=details.get("poster_path"),
                    runtime=details.get("runtime"),
                    language=details.get("language"),
                    cast=details.get("cast", []),
                    director=details.get("director"),
                    keywords=details.get("keywords", []),
                    vote_average=details.get("vote_average"),
                    popularity_score=popularity.get(int(row.movie_id), 0.0),
                )
            )
        session.add_all(records)
        session.flush()
        for record in records:
            details = metadata.get(record.id, {})
            if details:
                _replace_availability(session, record.id, details)
        session.commit()
        return len(records)


def _replace_availability(session: Session, movie_id: int, details: dict[str, object]) -> None:
    if "providers" not in details:
        return
    session.execute(delete(StreamingAvailability).where(StreamingAvailability.movie_id == movie_id))
    fetched_raw = details.get("fetched_at")
    fetched_at = datetime.fromisoformat(str(fetched_raw)) if fetched_raw else datetime.now(UTC)
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=UTC)
    providers = details.get("providers", [])
    if not isinstance(providers, list):
        return
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        session.add(
            StreamingAvailability(
                movie_id=movie_id,
                country=str(provider["country"]).upper(),
                provider_id=int(provider["provider_id"]),
                provider_name=str(provider["provider_name"]),
                access_type=str(provider["access_type"]),
                source_url=(str(provider["source_url"]) if provider.get("source_url") else None),
                fetched_at=fetched_at,
                expires_at=fetched_at + timedelta(days=1),
            )
        )
