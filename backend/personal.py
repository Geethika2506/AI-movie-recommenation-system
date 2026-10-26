"""Embedding-backed personal profile maintenance and hybrid ranking."""

from collections import defaultdict
from pathlib import Path

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.genres import movie_has_genre
from backend.models import (
    Movie,
    MovieEmbedding,
    PreferenceEvent,
    Profile,
    UserMovieState,
    UserProfileVector,
)
from backend.recommendations import recommend_for_profile
from recommender.collaborative import ImplicitALSRecommender
from recommender.embeddings import EMBEDDING_DIMENSIONS, EMBEDDING_FEATURE_VERSION
from recommender.features import ACTION_WEIGHTS
from recommender.hybrid import Candidate, HybridRecommender

PROFILE_FEATURE_VERSION = "embedding-taste-v1"
ONBOARDING_CONFIDENCE = 0.28


def _embeddings_for_movies(session: Session, movie_ids: set[int]) -> dict[int, np.ndarray]:
    if not movie_ids:
        return {}
    rows = session.execute(
        select(MovieEmbedding.movie_id, MovieEmbedding.embedding).where(
            MovieEmbedding.movie_id.in_(movie_ids)
        )
    ).all()
    return {movie_id: np.asarray(vector, dtype=np.float32) for movie_id, vector in rows}


def _top_embeddings_for_genre(session: Session, genre: str, limit: int) -> list[np.ndarray]:
    dialect = session.get_bind().dialect.name
    rows = session.execute(
        select(MovieEmbedding.embedding)
        .join(Movie, Movie.id == MovieEmbedding.movie_id)
        .where(movie_has_genre(genre, dialect=dialect))
        .order_by(Movie.popularity_score.desc().nullslast(), Movie.id)
        .limit(limit)
    ).all()
    return [np.asarray(row[0], dtype=np.float32) for row in rows]


def apply_onboarding_preferences(
    session: Session,
    profile_id: int,
    preferred_genres: list[str],
    disliked_genres: list[str],
) -> Profile:
    profile = session.get(Profile, profile_id)
    if profile is None:
        raise ValueError("profile not found")
    profile.seed_preferred_genres = preferred_genres
    profile.seed_disliked_genres = disliked_genres
    profile.recommendation_version += 1
    rebuild_taste_profile(session, profile_id)
    session.flush()
    return profile


def rebuild_taste_profile(session: Session, profile_id: int) -> UserProfileVector | None:
    profile = session.get(Profile, profile_id)
    if profile is None:
        raise ValueError("profile not found")
    events = list(
        session.scalars(
            select(PreferenceEvent)
            .where(
                PreferenceEvent.profile_id == profile_id,
                PreferenceEvent.undone.is_(False),
            )
            .order_by(PreferenceEvent.id)
        )
    )
    event_movie_ids = {event.movie_id for event in events}
    embeddings = _embeddings_for_movies(session, event_movie_ids)
    movies: dict[int, Movie] = {}
    if event_movie_ids:
        movies = {
            movie.id: movie
            for movie in session.scalars(select(Movie).where(Movie.id.in_(event_movie_ids)))
        }
    weighted: list[np.ndarray] = []
    weights: list[float] = []
    genre_evidence: defaultdict[str, float] = defaultdict(float)
    for genre in profile.seed_preferred_genres or []:
        genre_evidence[genre] += 2.0
    for genre in profile.seed_disliked_genres or []:
        genre_evidence[genre] -= 1.5
    for event in events:
        weight = ACTION_WEIGHTS.get(event.action, 0.0)
        vector = embeddings.get(event.movie_id)
        if vector is not None and weight:
            weighted.append(vector * weight)
            weights.append(abs(weight))
        movie = movies.get(event.movie_id)
        if movie and weight:
            for genre in movie.genres or []:
                genre_evidence[genre] += weight / max(len(movie.genres), 1)
    if not weighted and profile.seed_preferred_genres:
        for genre in profile.seed_preferred_genres:
            for vector in _top_embeddings_for_genre(session, genre, 3):
                weighted.append(vector * 2.0)
                weights.append(2.0)
        for genre in profile.seed_disliked_genres or []:
            for vector in _top_embeddings_for_genre(session, genre, 2):
                weighted.append(vector * -1.0)
                weights.append(1.0)
    if not weighted and not (profile.seed_preferred_genres or profile.seed_disliked_genres):
        return None
    if weighted:
        vector = np.sum(weighted, axis=0) / max(sum(weights), 1e-12)
        vector = vector / max(np.linalg.norm(vector), 1e-12)
        swipe_confidence = sum(weights) / (sum(weights) + 5.0)
    else:
        vector = np.zeros(EMBEDDING_DIMENSIONS, dtype=np.float32)
        swipe_confidence = 0.0
    confidence = max(swipe_confidence, ONBOARDING_CONFIDENCE if profile.seed_preferred_genres else 0.0)
    record = session.get(UserProfileVector, profile_id)
    values = vector.astype(float).tolist()
    preferred = sorted(
        set(profile.seed_preferred_genres or [])
        | {genre for genre, score in genre_evidence.items() if score > 0}
    )
    disliked = sorted(
        set(profile.seed_disliked_genres or [])
        | {genre for genre, score in genre_evidence.items() if score < 0}
    )
    if record:
        record.taste_vector = values
        record.preferred_genres = preferred
        record.disliked_genres = disliked
        record.confidence = confidence
        record.profile_version += 1
        record.feature_version = PROFILE_FEATURE_VERSION
    else:
        record = UserProfileVector(
            profile_id=profile_id,
            taste_vector=values,
            preferred_genres=preferred,
            disliked_genres=disliked,
            confidence=confidence,
            profile_version=1,
            feature_version=PROFILE_FEATURE_VERSION,
        )
        session.add(record)
    session.flush()
    return record


def embedding_hybrid_recommendations(
    session: Session,
    profile_id: int,
    limit: int,
    *,
    include_watched: bool = False,
    diversity: float = 0.18,
    exploration: float = 0.15,
    collaborative_artifact_dir: Path | None = None,
) -> list[tuple[Movie, float, str]]:
    profile_vector = session.get(UserProfileVector, profile_id)
    if profile_vector is None:
        profile_vector = rebuild_taste_profile(session, profile_id)
    if profile_vector is None:
        pool = min(500, max(limit * 5, 200))
        return recommend_for_profile(
            session,
            profile_id,
            limit,
            include_watched=include_watched,
            discovery=exploration,
            candidate_limit=pool,
        )

    states = list(
        session.scalars(select(UserMovieState).where(UserMovieState.profile_id == profile_id))
    )
    excluded = {
        state.movie_id
        for state in states
        if state.preference is not None or (state.watched and not include_watched)
    }
    excluded.update(
        session.scalars(
            select(PreferenceEvent.movie_id).where(
                PreferenceEvent.profile_id == profile_id,
                PreferenceEvent.undone.is_(False),
                PreferenceEvent.action.in_(["liked", "love", "disliked", "not_interested"]),
            )
        )
    )
    pool_size = min(600, max(limit * 5, 250))
    movie_rows = list(
        session.execute(
            select(Movie, MovieEmbedding)
            .join(MovieEmbedding, MovieEmbedding.movie_id == Movie.id)
            .order_by(Movie.popularity_score.desc().nullslast(), Movie.id)
            .limit(pool_size)
        )
    )
    taste = np.asarray(profile_vector.taste_vector, dtype=np.float32)
    if float(np.linalg.norm(taste)) < 1e-9:
        pool = min(500, max(limit * 5, 200))
        return recommend_for_profile(
            session,
            profile_id,
            limit,
            include_watched=include_watched,
            discovery=exploration,
            candidate_limit=pool,
        )
    popularities = np.asarray([movie.popularity_score or 0.0 for movie, _ in movie_rows])
    low = float(popularities.min()) if len(popularities) else 0.0
    span = float(np.ptp(popularities)) if len(popularities) else 0.0
    candidates: dict[str, list[Candidate]] = {
        "content": [],
        "collaborative": [],
        "popularity": [],
        "exploration": [],
    }
    movies: dict[int, Movie] = {}
    for movie, embedding in movie_rows:
        if movie.id in excluded:
            continue
        vector = np.asarray(embedding.embedding, dtype=np.float32)
        similarity = float(np.dot(taste, vector))
        popularity = ((movie.popularity_score or 0.0) - low) / span if span else 0.5
        novelty = 1.0 - popularity
        genres = set(movie.genres or [])
        matched = sorted(genres & set(profile_vector.preferred_genres))
        if matched and profile_vector.confidence <= ONBOARDING_CONFIDENCE + 0.05:
            reason = f"Based on your interest in {matched[0]}"
        elif matched:
            reason = f"Matches your interest in {matched[0]}"
        else:
            reason = "Similar to movies you explicitly enjoyed"
        movies[movie.id] = movie
        candidates["content"].append(Candidate(movie.id, similarity, "content", reason, vector))
        candidates["popularity"].append(
            Candidate(movie.id, popularity, "popularity", "Well rated in the catalog", vector)
        )
        candidates["exploration"].append(
            Candidate(
                movie.id,
                novelty,
                "exploration",
                "A less familiar eligible discovery",
                vector,
            )
        )
    if collaborative_artifact_dir and (collaborative_artifact_dir / "manifest.json").exists():
        try:
            collaborative = ImplicitALSRecommender.load(collaborative_artifact_dir)
            collaborative_rows = collaborative.recommend("app", profile_id, 200, exclude=excluded)
            missing_ids = {movie_id for movie_id, _score in collaborative_rows} - set(movies)
            for movie in session.scalars(select(Movie).where(Movie.id.in_(missing_ids))):
                movies[movie.id] = movie
            candidates["collaborative"] = [
                Candidate(
                    movie_id,
                    score,
                    "collaborative",
                    "Users with related feedback also engaged with this movie",
                )
                for movie_id, score in collaborative_rows
                if movie_id in movies
            ]
        except (OSError, ValueError, RuntimeError):
            candidates["collaborative"] = []
    weights = {
        "content": max(0.35, 0.55 - exploration * 0.2),
        "collaborative": 0.25 if candidates["collaborative"] else 0.0,
        "popularity": 0.2,
        "exploration": max(0.05, exploration),
    }
    hybrid = HybridRecommender(weights=weights, diversity=diversity)
    ranked = hybrid.combine(candidates, limit=limit, exclude=excluded)
    return [(movies[item.movie_id], item.score, item.reason) for item in ranked]


def embedding_versions(session: Session) -> tuple[str, str]:
    row = session.scalar(select(MovieEmbedding).limit(1))
    return (
        row.embedding_model_version if row else "none",
        row.feature_version if row else EMBEDDING_FEATURE_VERSION,
    )
