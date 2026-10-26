from pathlib import Path

from sqlalchemy import func, select

from backend.db import Base, make_engine, make_session_factory
from backend.models import Movie, MovieEmbedding, PreferenceEvent, Profile
from backend.personal import embedding_hybrid_recommendations, rebuild_taste_profile
from recommender.data_governance import DataPolicyManifest
from recommender.embeddings import (
    DeterministicTestEncoder,
    generate_movie_embeddings,
    movie_embedding_text,
)

ROOT = Path(__file__).parents[1]


def test_embedding_generation_and_personal_profile_are_deterministic(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'embeddings.db'}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    policy = DataPolicyManifest.load(ROOT / "configs/data_sources.json")
    with factory() as session:
        session.add_all(
            [
                Movie(id=1, title="Space One", genres=["Sci-Fi"], overview="TMDB text one"),
                Movie(id=2, title="Space Two", genres=["Sci-Fi"], overview="TMDB text two"),
                Movie(id=3, title="Comedy", genres=["Comedy"], overview="TMDB text three"),
            ]
        )
        profile = Profile(name="Taste", owner_key_hash="test-owner")
        session.add(profile)
        session.flush()
        session.add(
            PreferenceEvent(
                profile_id=profile.id,
                movie_id=1,
                request_id="organic-like-1",
                action="liked",
                previous_state=None,
                new_state={"preference": "liked"},
            )
        )
        session.commit()

        assert movie_embedding_text(session.get(Movie, 1)) == "Title: Space One. Genres: Sci-Fi."
        assert (
            generate_movie_embeddings(session, DeterministicTestEncoder(), policy, batch_size=2)
            == 3
        )
        assert (
            generate_movie_embeddings(session, DeterministicTestEncoder(), policy, batch_size=2)
            == 0
        )
        assert session.scalar(select(func.count()).select_from(MovieEmbedding)) == 3

        vector = rebuild_taste_profile(session, profile.id)
        assert vector is not None
        assert vector.confidence > 0
        recommendations = embedding_hybrid_recommendations(session, profile.id, 2)
        assert recommendations
        assert recommendations[0][0].id != 1
        assert all(reason for _movie, _score, reason in recommendations)


def _axis(index: int) -> list[float]:
    values = [0.0] * 384
    values[index] = 1.0
    return values


def test_slider_mixes_similar_titles_with_random_ones(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'mix.db'}")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        session.add_all(
            [
                Movie(id=1, title="Liked", genres=["Sci-Fi"]),
                Movie(id=2, title="Also Sci-Fi", genres=["Sci-Fi"]),
                Movie(id=3, title="More Sci-Fi", genres=["Sci-Fi"]),
                *[
                    Movie(id=movie_id, title=f"Other {movie_id}", genres=["Drama"])
                    for movie_id in range(4, 12)
                ],
            ]
        )
        profile = Profile(name="Taste", owner_key_hash="mix-owner")
        session.add(profile)
        session.flush()
        session.add_all(
            [
                MovieEmbedding(
                    movie_id=1,
                    embedding=_axis(0),
                    embedding_model_version="test",
                    feature_version="test",
                    source_fingerprint="a",
                ),
                MovieEmbedding(
                    movie_id=2,
                    embedding=_axis(0),
                    embedding_model_version="test",
                    feature_version="test",
                    source_fingerprint="b",
                ),
                MovieEmbedding(
                    movie_id=3,
                    embedding=_axis(0),
                    embedding_model_version="test",
                    feature_version="test",
                    source_fingerprint="c",
                ),
                *[
                    MovieEmbedding(
                        movie_id=movie_id,
                        embedding=_axis(1),
                        embedding_model_version="test",
                        feature_version="test",
                        source_fingerprint=str(movie_id),
                    )
                    for movie_id in range(4, 12)
                ],
                PreferenceEvent(
                    profile_id=profile.id,
                    movie_id=1,
                    request_id="like-axis",
                    action="liked",
                    previous_state=None,
                    new_state={"preference": "liked"},
                ),
            ]
        )
        session.commit()
        vector = rebuild_taste_profile(session, profile.id)
        assert vector is not None

        familiar = embedding_hybrid_recommendations(session, profile.id, 6, exploration=0)
        assert [movie.id for movie, _score, _reason in familiar[:2]] == [2, 3]
        assert all("random" not in reason.lower() for _movie, _score, reason in familiar[:2])

        middle = embedding_hybrid_recommendations(session, profile.id, 4, exploration=0.5)
        middle_ids = [movie.id for movie, _score, _reason in middle]
        assert sum(movie_id in {2, 3} for movie_id in middle_ids) == 2
        assert {2, 3}.issubset(middle_ids)
        assert sum("random" in reason.lower() for _movie, _score, reason in middle) == 2

        discovered = embedding_hybrid_recommendations(session, profile.id, 6, exploration=1)
        assert all("random" in reason.lower() for _movie, _score, reason in discovered)
        assert [movie.id for movie, _score, _reason in discovered] != [
            movie.id for movie, _score, _reason in familiar
        ]
