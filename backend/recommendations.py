from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models import Interaction, InteractionKind, Movie, Rating, UserMovieState


def recommend_for_profile(
    session: Session,
    profile_id: int,
    limit: int,
    exclude: set[int] | None = None,
    include_watched: bool = False,
    discovery: float = 0.35,
) -> list[tuple[Movie, float, str]]:
    movies = session.scalars(select(Movie)).all()
    ratings = session.execute(
        select(Rating.movie_id, Rating.value).where(Rating.profile_id == profile_id)
    ).all()
    blocked = set(exclude or set()) | {movie_id for movie_id, _ in ratings}
    blocked_kinds = [InteractionKind.DISMISSED]
    if not include_watched:
        blocked_kinds.append(InteractionKind.WATCHED)
    blocked.update(
        session.scalars(
            select(Interaction.movie_id).where(
                Interaction.profile_id == profile_id,
                Interaction.kind.in_(blocked_kinds),
            )
        )
    )
    states = list(
        session.scalars(select(UserMovieState).where(UserMovieState.profile_id == profile_id))
    )
    blocked.update(state.movie_id for state in states if state.watched)
    liked_genres: defaultdict[str, float] = defaultdict(float)
    genre_counts: defaultdict[str, float] = defaultdict(float)
    movie_map = {movie.id: movie for movie in movies}
    for movie_id, value in ratings:
        rated_movie = movie_map.get(movie_id)
        # MovieLens convention used here: >=4 positive, <=2.5 negative, 3-3.5 neutral.
        signal = max(-1.0, min(1.0, (value - 3.25) / 1.25)) if value >= 4 or value <= 2.5 else 0
        for genre in rated_movie.genres if rated_movie else []:
            liked_genres[genre] += signal
            genre_counts[genre] += abs(signal)
    liked_movie_ids: list[int] = []
    disliked_genres: set[str] = set()
    for state in states:
        state_movie = movie_map.get(state.movie_id)
        if not state_movie:
            continue
        if state.preference == "liked":
            signal = 1.0
        elif state.preference == "disliked":
            signal = -1.0
        else:
            signal = 0.2 if state.watchlist else 0.0
        if state.preference == "liked":
            liked_movie_ids.append(state.movie_id)
        for genre in state_movie.genres or []:
            liked_genres[genre] += signal
            genre_counts[genre] += abs(signal)
            if signal < 0:
                disliked_genres.add(genre)
    aggregate = dict(
        session.execute(
            select(
                Rating.movie_id,
                (func.avg(Rating.value) * func.count(Rating.id) + 3.5 * 10)
                / (func.count(Rating.id) + 10),
            ).group_by(Rating.movie_id)
        ).all()
    )
    mix = min(1.0, max(0.0, discovery))
    popularities = [
        float(aggregate.get(movie.id, movie.popularity_score or 0.0)) for movie in movies
    ]
    low = min(popularities) if popularities else 0.0
    high = max(popularities) if popularities else 1.0
    span = high - low
    ranked = []
    for movie in movies:
        if movie.id in blocked:
            continue
        popularity = float(aggregate.get(movie.id, movie.popularity_score or 0.0))
        popularity_norm = (popularity - low) / span if span > 1e-9 else 0.5
        genre_scores = [
            liked_genres.get(genre, 0.0) / max(genre_counts.get(genre, 1.0), 1.0)
            for genre in movie.genres or []
        ]
        taste = sum(genre_scores) / len(genre_scores) if genre_scores else 0.0
        familiar = 0.65 * popularity_norm + 1.55 * taste
        adventurous = 0.2 * popularity_norm + 1.7 * taste + 0.45 * (1.0 - popularity_norm)
        score = (1.0 - mix) * familiar + mix * adventurous
        if taste < 0:
            score += taste * (1.1 + 0.6 * (1.0 - mix))
        positive = [genre for genre in movie.genres or [] if liked_genres.get(genre, 0) > 0]
        negative = [genre for genre in movie.genres or [] if genre in disliked_genres]
        if positive and liked_movie_ids:
            reason = (
                f"Similar to {len(liked_movie_ids)} movie"
                f"{'s' if len(liked_movie_ids) != 1 else ''} you liked; matches {positive[0]}"
            )
        elif mix > 0.6 and popularity_norm < 0.45:
            reason = "A less familiar pick from your current discovery mix"
        elif negative:
            reason = f"Exploration pick despite mixed signals for {negative[0]}"
        else:
            reason = "Popular with CineMatch viewers"
        ranked.append((movie, score, reason))
    return sorted(ranked, key=lambda item: (-item[1], item[0].id))[:limit]


def group_recommendations(
    session: Session,
    profile_ids: list[int],
    limit: int,
    mean_weight: float,
    include_watched: bool = False,
    genres: list[str] | None = None,
    language: str | None = None,
    max_runtime: int | None = None,
) -> list[tuple[Movie, float, str]]:
    movies = list(session.scalars(select(Movie)))
    watched = set(
        session.scalars(
            select(UserMovieState.movie_id).where(
                UserMovieState.profile_id.in_(profile_ids), UserMovieState.watched.is_(True)
            )
        )
    )
    candidates = [
        movie
        for movie in movies
        if (include_watched or movie.id not in watched)
        and (not genres or any(genre in movie.genres for genre in genres))
        and (not language or movie.language == language)
        and (not max_runtime or movie.runtime is None or movie.runtime <= max_runtime)
    ]
    if not candidates:
        return []

    raw_by_profile: list[dict[int, float]] = []
    for profile_id in profile_ids:
        rows = recommend_for_profile(
            session,
            profile_id,
            len(movies),
            include_watched=include_watched,
        )
        raw = {movie.id: score for movie, score, _ in rows}
        available = [raw.get(movie.id, 0.0) for movie in candidates]
        low, high = min(available), max(available)
        span = high - low
        raw_by_profile.append(
            {
                movie.id: (raw.get(movie.id, low) - low) / span if span > 1e-9 else 0.5
                for movie in candidates
            }
        )

    ranked: list[tuple[Movie, float, str]] = []
    for movie in candidates:
        member_scores = [scores[movie.id] for scores in raw_by_profile]
        average = sum(member_scores) / len(member_scores)
        minimum = min(member_scores)
        score = aggregate_member_scores(member_scores, mean_weight)
        spread = max(member_scores) - minimum
        reason = (
            "Best available compromise across conflicting preferences"
            if spread > 0.55
            else f"Group fit: {average:.0%} average, {minimum:.0%} least-satisfied member"
        )
        ranked.append((movie, score, reason))
    return sorted(ranked, key=lambda row: (-row[1], row[0].id))[:limit]


def aggregate_member_scores(member_scores: list[float], mean_weight: float) -> float:
    """Combine normalized [0, 1] member scores with an interpretable mean/min blend."""
    if not member_scores:
        raise ValueError("at least one member score is required")
    if not 0 <= mean_weight <= 1:
        raise ValueError("mean_weight must be between zero and one")
    average = sum(member_scores) / len(member_scores)
    return mean_weight * average + (1 - mean_weight) * min(member_scores)


def discovery_for_profile(
    session: Session, profile_id: int, limit: int
) -> list[tuple[Movie, float, str]]:
    """Round-robin ranked candidates across genre/era buckets for simple exploration."""
    movie_count = session.scalar(select(func.count()).select_from(Movie)) or 0
    ranked = recommend_for_profile(session, profile_id, movie_count)
    buckets: dict[tuple[str, int], list[tuple[Movie, float, str]]] = {}
    for item in ranked:
        movie = item[0]
        key = ((movie.genres or ["Other"])[0], (movie.year or 1900) // 10)
        buckets.setdefault(key, []).append(item)
    selected: list[tuple[Movie, float, str]] = []
    ordered_keys = sorted(buckets, key=lambda key: (key[1], key[0]))
    while len(selected) < limit and ordered_keys:
        next_keys = []
        for key in ordered_keys:
            if buckets[key]:
                selected.append(buckets[key].pop(0))
                if len(selected) == limit:
                    break
            if buckets[key]:
                next_keys.append(key)
        ordered_keys = next_keys
    return selected


def similar_movies(session: Session, movie: Movie, limit: int) -> list[tuple[Movie, float, str]]:
    source = set(movie.genres or [])
    candidates = []
    for candidate in session.scalars(select(Movie).where(Movie.id != movie.id)):
        genres = set(candidate.genres or [])
        union = source | genres
        similarity = len(source & genres) / len(union) if union else 0.0
        if similarity:
            reason = f"Shares {', '.join(sorted(source & genres))}"
            candidates.append((candidate, similarity, reason))
    return sorted(candidates, key=lambda item: (-item[1], item[0].id))[:limit]
