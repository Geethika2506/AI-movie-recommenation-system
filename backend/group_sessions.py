"""Fair, session-scoped group candidate generation and ranking."""

import math
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import GroupSession, GroupVote, Movie, StreamingAvailability
from backend.personal import embedding_hybrid_recommendations


@dataclass(frozen=True)
class GroupRankedMovie:
    movie: Movie
    score: float
    reason: str
    member_scores: tuple[float, ...]


def aggregate_group_scores(scores: list[float], algorithm: str) -> float:
    if not scores:
        raise ValueError("at least one member score is required")
    mean = sum(scores) / len(scores)
    minimum = min(scores)
    spread = max(scores) - minimum
    if algorithm == "average":
        return mean
    if algorithm == "least_misery":
        return minimum
    if algorithm == "fair_consensus":
        return 0.55 * mean + 0.45 * minimum - 0.10 * spread
    if algorithm == "nash":
        return math.prod(max(score, 1e-6) for score in scores) ** (1 / len(scores))
    raise ValueError(f"unsupported group algorithm: {algorithm}")


def rank_group_session(
    db: Session, group_session: GroupSession, limit: int = 40
) -> list[GroupRankedMovie]:
    participants = list(dict.fromkeys(group_session.participant_profile_ids))
    if not participants:
        return []
    filters = group_session.filters or {}
    exclusions = set(_int_list(filters.get("exclusions")))
    per_member: dict[int, dict[int, float]] = {}
    represented: dict[int, set[int]] = {}
    movie_map: dict[int, Movie] = {}
    candidate_ids: set[int] = set()
    for profile_id in participants:
        rows = embedding_hybrid_recommendations(db, profile_id, 80, exploration=0.15)
        scores = _minmax({movie.id: score for movie, score, _reason in rows})
        per_member[profile_id] = scores
        represented[profile_id] = {movie.id for movie, _score, _reason in rows[:20]}
        candidate_ids.update(represented[profile_id])
        movie_map.update({movie.id: movie for movie, _score, _reason in rows})
    candidate_ids -= exclusions
    candidate_ids = _eligible_candidates(db, candidate_ids, filters)

    votes = list(db.scalars(select(GroupVote).where(GroupVote.session_id == group_session.id)))
    vote_map = {(vote.profile_id, vote.movie_id): vote.action for vote in votes}
    vetoed = {vote.movie_id for vote in votes if vote.action == "veto"}
    candidate_ids -= vetoed

    ranked = []
    for movie_id in candidate_ids:
        member_scores = []
        for profile_id in participants:
            score = per_member[profile_id].get(movie_id, 0.35)
            action = vote_map.get((profile_id, movie_id))
            score += {"love": 0.25, "like": 0.15, "dislike": -0.25}.get(action or "", 0.0)
            member_scores.append(min(1.0, max(0.0, score)))
        group_score = aggregate_group_scores(member_scores, group_session.ranking_algorithm)
        spread = max(member_scores) - min(member_scores)
        if spread <= 0.2:
            reason = "Strong match across all participating members"
        elif min(member_scores) >= 0.5:
            reason = "A fair compromise with no low member score"
        else:
            reason = "Balances average fit with the least-satisfied member"
        ranked.append(
            GroupRankedMovie(
                movie=movie_map[movie_id],
                score=group_score,
                reason=reason,
                member_scores=tuple(member_scores),
            )
        )
    ranked.sort(key=lambda row: (-row.score, row.movie.id))
    output = ranked[:limit]
    group_session.exposure_by_member = {
        str(profile_id): [row.movie.id for row in output if row.movie.id in represented[profile_id]]
        for profile_id in participants
    }
    return output


def consensus_reached(db: Session, group_session: GroupSession, movie_id: int) -> bool:
    participants = set(group_session.participant_profile_ids)
    votes = {
        vote.profile_id: vote.action
        for vote in db.scalars(
            select(GroupVote).where(
                GroupVote.session_id == group_session.id,
                GroupVote.movie_id == movie_id,
            )
        )
        if vote.profile_id in participants
    }
    if any(action == "veto" for action in votes.values()):
        return False
    positive = sum(action in {"like", "love"} for action in votes.values())
    policy = group_session.consensus_policy
    if policy == "unanimous":
        return len(votes) == len(participants) and positive == len(participants)
    if policy == "strong":
        return len(votes) == len(participants) and positive / len(participants) >= 0.75
    if policy == "veto":
        return len(votes) == len(participants) and positive > len(participants) / 2
    return positive > len(participants) / 2


def _minmax(values: dict[int, float]) -> dict[int, float]:
    if not values:
        return {}
    low, high = min(values.values()), max(values.values())
    if high - low <= 1e-12:
        return {movie_id: 0.5 for movie_id in values}
    return {movie_id: (value - low) / (high - low) for movie_id, value in values.items()}


def _eligible_candidates(
    db: Session, candidate_ids: set[int], filters: dict[str, object]
) -> set[int]:
    if not candidate_ids:
        return set()
    query = select(Movie).where(Movie.id.in_(candidate_ids))
    max_runtime = filters.get("max_runtime")
    if isinstance(max_runtime, (int, float)):
        query = query.where((Movie.runtime.is_(None)) | (Movie.runtime <= int(max_runtime)))
    languages = [str(value) for value in _object_list(filters.get("languages"))]
    if languages:
        query = query.where(Movie.language.in_(languages))
    movies = list(db.scalars(query))
    genres = {str(value) for value in _object_list(filters.get("genres"))}
    eligible = {
        movie.id for movie in movies if not genres or bool(genres.intersection(movie.genres or []))
    }
    country = filters.get("country")
    providers = _int_list(filters.get("provider_ids"))
    if country and providers:
        available = set(
            db.scalars(
                select(StreamingAvailability.movie_id).where(
                    StreamingAvailability.movie_id.in_(eligible),
                    StreamingAvailability.country == str(country).upper(),
                    StreamingAvailability.provider_id.in_(providers),
                )
            )
        )
        eligible &= available
    return eligible


def _object_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _int_list(value: object) -> list[int]:
    return [
        int(item)
        for item in _object_list(value)
        if isinstance(item, (int, str)) and str(item).isdigit()
    ]
