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
