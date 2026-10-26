"""Embedding-backed personal profile maintenance and hybrid ranking."""

import random
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
from backend.recommendations import (
    discovery_shuffle_seed,
    match_count_for_discovery,
    mix_discovery_slots,
    recommend_for_profile,
)
from recommender.collaborative import ImplicitALSRecommender
from recommender.embeddings import EMBEDDING_DIMENSIONS, EMBEDDING_FEATURE_VERSION
from recommender.features import ACTION_WEIGHTS

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
    diversity: float = 0.18,  # kept for callers; the slider uses a hard match/random mix
    exploration: float = 0.15,
    collaborative_artifact_dir: Path | None = None,
) -> list[tuple[Movie, float, str]]:
    profile_vector = session.get(UserProfileVector, profile_id)
    if profile_vector is None:
        profile_vector = rebuild_taste_profile(session, profile_id)
    if profile_vector is None:
        return recommend_for_profile(
            session,
            profile_id,
            limit,
            include_watched=include_watched,
            discovery=exploration,
            slot_mix=True,
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
    pool_size = min(2000, max(limit * 8, 400))
    movie_rows = list(
        session.execute(
            select(Movie, MovieEmbedding)
            .join(MovieEmbedding, MovieEmbedding.movie_id == Movie.id)
            .order_by(Movie.popularity_score.desc().nullslast(), Movie.id)
            .limit(pool_size)
        )
    )
    taste = np.asarray(profile_vector.taste_vector, dtype=np.float32)
    if float(np.linalg.norm(taste)) < 1e-9 or not movie_rows:
        return recommend_for_profile(
            session,
            profile_id,
            limit,
            include_watched=include_watched,
            discovery=exploration,
            slot_mix=True,
        )
    collab_bonus = _collaborative_bonus(
        profile_id,
        excluded,
        collaborative_artifact_dir,
    )
    disliked = set(profile_vector.disliked_genres or [])
    preferred = set(profile_vector.preferred_genres or [])
    scored: list[tuple[Movie, float, str]] = []
    for movie, embedding in movie_rows:
        if movie.id in excluded:
            continue
        genres = set(movie.genres or [])
        if disliked and genres and genres <= disliked:
            continue
        vector = np.asarray(embedding.embedding, dtype=np.float32)
        similarity = float(np.dot(taste, vector)) + 0.08 * collab_bonus.get(movie.id, 0.0)
        matched = sorted(genres & preferred)
        if matched and profile_vector.confidence <= ONBOARDING_CONFIDENCE + 0.05:
            reason = f"Based on your interest in {matched[0]}"
        elif matched:
            reason = f"Matches your interest in {matched[0]}"
        else:
            reason = "Similar to movies you explicitly enjoyed"
        scored.append((movie, similarity, reason))
    scored.sort(key=lambda item: (-item[1], -(item[0].popularity_score or 0.0), item[0].id))
    return _mix_similar_and_random(
        session,
        profile_id,
        scored,
        excluded,
        disliked,
        exploration,
        limit,
    )


def _collaborative_bonus(
    profile_id: int,
    excluded: set[int],
    artifact_dir: Path | None,
) -> dict[int, float]:
    """Small tie-break from implicit ALS. Taste similarity still leads familiar picks."""
    if artifact_dir is None or not (artifact_dir / "manifest.json").exists():
        return {}
    try:
        collaborative = ImplicitALSRecommender.load(artifact_dir)
        rows = collaborative.recommend("app", profile_id, 200, exclude=excluded)
    except (OSError, ValueError, RuntimeError):
        return {}
    if not rows:
        return {}
    scores = [score for _movie_id, score in rows]
    low, high = min(scores), max(scores)
    span = high - low
    return {
        movie_id: ((score - low) / span if span else 0.5)
        for movie_id, score in rows
    }


def _mix_similar_and_random(
    session: Session,
    profile_id: int,
    scored: list[tuple[Movie, float, str]],
    excluded: set[int],
    disliked: set[str],
    exploration: float,
    limit: int,
) -> list[tuple[Movie, float, str]]:
    """Fill familiar slots from taste similarity and discovery slots from a shuffle."""
    n_match = match_count_for_discovery(limit, exploration)
    best = scored[0][1] if scored else 0.0
    cutoff = max(0.15, best * 0.72) if best > 0 else 0.15
    strong = [item for item in scored if item[1] >= cutoff]
    matches = scored
    similarity = {item[0].id: item[1] for item in scored}
    eligible_ids = [
        movie_id
        for movie_id in session.scalars(select(Movie.id))
        if movie_id not in excluded
    ]
    if n_match == 0:
        random_ids = list(eligible_ids)
    else:
        reserved = {item[0].id for item in strong[:n_match]}
        distant = [
            movie_id
            for movie_id in eligible_ids
            if movie_id not in reserved and similarity.get(movie_id, 0.0) < cutoff
        ]
        outside = [movie_id for movie_id in eligible_ids if movie_id not in reserved]
        random_ids = distant if distant else outside
    random.Random(discovery_shuffle_seed(profile_id, exploration)).shuffle(random_ids)
    known = {item[0].id: item[0] for item in scored}
    window = random_ids[: max(limit * 3, limit)]
    missing = [movie_id for movie_id in window if movie_id not in known]
    if missing:
        for movie in session.scalars(select(Movie).where(Movie.id.in_(missing))):
            known[movie.id] = movie
    random_rows: list[tuple[Movie, float, str]] = []
    for movie_id in window:
        picked = known.get(movie_id)
        if picked is None:
            continue
        genres = set(picked.genres or [])
        if disliked and genres and genres <= disliked:
            continue
        random_rows.append((picked, 0.0, "A random pick, outside your closest matches"))
    mixed = mix_discovery_slots(
        matches,
        random_rows,
        limit,
        exploration,
        lambda item: item[0].id,
    )
    output: list[tuple[Movie, float, str]] = []
    for item, slot in mixed:
        movie, score, reason = item
        if slot == "random":
            output.append((movie, -0.2, "A random pick, outside your closest matches"))
        else:
            output.append((movie, score, reason))
    return output


def embedding_versions(session: Session) -> tuple[str, str]:
    row = session.scalar(select(MovieEmbedding).limit(1))
    return (
        row.embedding_model_version if row else "none",
        row.feature_version if row else EMBEDDING_FEATURE_VERSION,
    )
