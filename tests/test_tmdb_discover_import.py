from pathlib import Path

from sqlalchemy import select

from backend.db import Base, make_engine, make_session_factory
from backend.models import Movie
from backend.tmdb_catalog import import_discover_movies, normalize_tmdb_genres


class FakeTMDBClient:
    enabled = True

    def movie_genre_map(self) -> dict[int, str]:
        return {878: "Science Fiction", 28: "Action"}

    def discover_movies(self, *, release_date_gte: str, page: int = 1, sort_by: str = "popularity.desc"):
        _ = release_date_gte, sort_by
        if page > 1:
            return {"page": page, "total_pages": 1, "results": []}
        return {
            "page": 1,
            "total_pages": 1,
            "results": [
                {
                    "id": 999001,
                    "title": "Future Hit",
                    "release_date": "2024-06-01",
                    "genre_ids": [878, 28],
                    "popularity": 120.0,
                    "vote_average": 7.8,
                    "overview": "A recent sci-fi action release.",
                    "poster_path": "/future.jpg",
                    "original_language": "en",
                }
            ],
        }


def test_normalize_tmdb_genres_maps_scifi() -> None:
    assert normalize_tmdb_genres(["Science Fiction", "Action"]) == ["Sci-Fi", "Action"]


def test_import_discover_inserts_and_updates_by_tmdb_id(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'tmdb-discover.db'}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    client = FakeTMDBClient()

    with factory() as session:
        session.add(
            Movie(
                id=1,
                title="Legacy",
                year=2000,
                genres=["Drama"],
                tmdb_id=999001,
                popularity_score=0.1,
            )
        )
        session.commit()
        inserted, updated = import_discover_movies(session, client, years=3, limit=10)
        assert inserted == 0
        assert updated == 1
        row = session.scalar(select(Movie).where(Movie.tmdb_id == 999001))
        assert row is not None
        assert row.title == "Future Hit"
        assert row.genres == ["Sci-Fi", "Action"]
        assert row.year == 2024

    with factory() as session:
        inserted, updated = import_discover_movies(session, client, years=3, limit=10)
        assert inserted == 0
        assert updated == 1
        assert session.scalar(select(Movie).where(Movie.tmdb_id == 999001)).title == "Future Hit"

    with factory() as session:
        session.execute(Movie.__table__.delete())
        session.commit()
        inserted, updated = import_discover_movies(session, client, years=3, limit=10)
        assert inserted == 1
        assert updated == 0
        row = session.scalar(select(Movie))
        assert row is not None
        assert row.id == 1
        assert row.tmdb_id == 999001
