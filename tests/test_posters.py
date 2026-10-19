from pathlib import Path

import httpx
from sqlalchemy import select

from backend.catalog import import_prepared_catalog
from backend.db import Base, make_engine, make_session_factory
from backend.models import Movie
from backend.posters import (
    candidate_titles,
    enrich_wikipedia_posters,
    hydrate_posters,
    wikipedia_cache_path,
)
from backend.schemas import MovieOut
from tests.test_catalog_tmdb import prepared_catalog


def test_candidate_titles_move_leading_articles() -> None:
    names = candidate_titles("Shawshank Redemption, The", 1994)
    assert names[0] == "The Shawshank Redemption (1994 film)"
    assert "Shawshank Redemption, The" in names


def test_wikipedia_posters_fill_missing_absolute_urls(tmp_path: Path) -> None:
    data_dir = prepared_catalog(tmp_path)
    engine = make_engine(f"sqlite:///{tmp_path / 'catalog.db'}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    assert import_prepared_catalog(factory, data_dir) == 1

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "media-imdb.com" in url:
            return httpx.Response(
                200,
                json={
                    "d": [
                        {
                            "id": "tt0114709",
                            "l": "Toy Story",
                            "i": {"imageUrl": "https://m.media-amazon.com/images/M/toy.jpg?utm=1"},
                        }
                    ]
                },
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert enrich_wikipedia_posters(factory, data_dir, client=client) == 1
    with factory() as session:
        movie = session.scalar(select(Movie).where(Movie.id == 1))
        assert movie is not None
        assert movie.poster_path == "https://m.media-amazon.com/images/M/toy.jpg"
        assert MovieOut.model_validate(movie).poster_url == movie.poster_path


def test_hydrate_posters_uses_disk_cache_without_network(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'catalog.db'}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    cache = wikipedia_cache_path(tmp_path)
    cache.parent.mkdir(parents=True)
    cache.write_text(
        '{"movie_id": 1, "poster_path": "https://images.example/poster.jpg"}\n'
    )
    with factory() as session:
        movie = Movie(id=1, title="Toy Story", year=1995, genres=["Animation"])
        session.add(movie)
        session.commit()
        hydrate_posters(session, [movie], tmp_path, enabled=False, client=httpx.Client())
        assert movie.poster_path == "https://images.example/poster.jpg"


def test_stale_missing_cache_is_retried(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'catalog.db'}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    data_dir = prepared_catalog(tmp_path)
    cache = wikipedia_cache_path(data_dir)
    cache.write_text('{"movie_id": 1, "poster_path": null}\n')

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"d": [{"id": "tt0114709", "i": {"imageUrl": "https://img.example/found.jpg"}}]},
        )

    with factory() as session:
        movie = Movie(id=1, title="Toy Story", year=1995, genres=["Animation"])
        session.add(movie)
        session.commit()
        hydrate_posters(
            session,
            [movie],
            data_dir,
            enabled=True,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        assert movie.poster_path == "https://img.example/found.jpg"
