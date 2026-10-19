import json
from pathlib import Path

import pandas as pd
from sqlalchemy import select

from backend.catalog import import_prepared_catalog
from backend.db import Base, make_engine, make_session_factory
from backend.models import Movie
from backend.schemas import MovieOut


def prepared_catalog(root: Path) -> Path:
    data_dir = root / "data"
    prepared = data_dir / "processed" / "latest-small"
    prepared.mkdir(parents=True)
    pd.DataFrame(
        [{"movie_id": 1, "title": "Toy Story (1995)", "genres": "Animation|Comedy"}]
    ).to_csv(prepared / "movies.csv", index=False)
    pd.DataFrame([{"movie_id": 1, "imdb_id": 114709, "tmdb_id": 862}]).to_csv(
        prepared / "links.csv", index=False
    )
    return data_dir


def test_movielens_tmdb_mapping_and_metadata_backfill(tmp_path: Path) -> None:
    data_dir = prepared_catalog(tmp_path)
    engine = make_engine(f"sqlite:///{tmp_path / 'catalog.db'}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)

    assert import_prepared_catalog(factory, data_dir) == 1
    with factory() as session:
        movie = session.get(Movie, 1)
        assert movie is not None
        assert movie.tmdb_id == 862
        assert movie.poster_path is None

    metadata = {
        "movie_id": 1,
        "tmdb_id": 862,
        "poster_path": "/poster.jpg",
        "overview": "Toys come alive.",
        "runtime": 81,
        "language": "en",
        "cast": ["Tom Hanks"],
        "director": "John Lasseter",
    }
    path = data_dir / "processed" / "latest-small" / "tmdb.jsonl"
    path.write_text(json.dumps(metadata) + "\n")

    assert import_prepared_catalog(factory, data_dir) == 1
    with factory() as session:
        movie = session.scalar(select(Movie).where(Movie.id == 1))
        assert movie is not None
        assert movie.poster_path == "/poster.jpg"
        output = MovieOut.model_validate(movie)
        assert output.poster_url == "https://image.tmdb.org/t/p/w500/poster.jpg"


def test_poster_url_is_nullable_and_accepts_absolute_urls() -> None:
    missing = Movie(id=1, title="Missing", genres=[], cast=[])
    absolute = Movie(
        id=2,
        title="Absolute",
        genres=[],
        cast=[],
        poster_path="https://images.example/poster.jpg",
    )
    assert MovieOut.model_validate(missing).poster_url is None
    assert MovieOut.model_validate(absolute).poster_url == "https://images.example/poster.jpg"
