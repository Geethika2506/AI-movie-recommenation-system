"""Deterministic feature transforms used by both offline training and serving."""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from math import sqrt

FEATURE_VERSION = "taste-v1"

ACTION_WEIGHTS: dict[str, float] = {
    "disliked": -1.0,
    "not_interested": -1.0,
    "liked": 1.0,
    "love": 1.5,
    "watchlist": 0.35,
    "watched": 0.0,
    "skip": 0.0,
}


@dataclass(frozen=True)
class TasteSignal:
    action_id: str
    movie_id: int
    action: str
    genres: tuple[str, ...] = ()
    undone: bool = False


@dataclass(frozen=True)
class TasteFeatures:
    version: str
    event_count: int
    positive_count: int
    negative_count: int
    evidence_weight: float
    confidence: float
    genre_affinity: tuple[tuple[str, float], ...]


def build_taste_features(signals: Iterable[TasteSignal]) -> TasteFeatures:
    """Build stable features while removing duplicate and undone training signals."""
    unique: dict[str, TasteSignal] = {}
    for signal in signals:
        previous = unique.get(signal.action_id)
        if previous is not None and previous != signal:
            raise ValueError(f"conflicting duplicate action_id: {signal.action_id}")
        unique[signal.action_id] = signal

    active = sorted(
        (signal for signal in unique.values() if not signal.undone),
        key=lambda signal: signal.action_id,
    )
    affinity: defaultdict[str, float] = defaultdict(float)
    positive = 0
    negative = 0
    evidence = 0.0
    for signal in active:
        if signal.action not in ACTION_WEIGHTS:
            raise ValueError(f"unsupported action: {signal.action}")
        weight = ACTION_WEIGHTS[signal.action]
        positive += int(weight > 0)
        negative += int(weight < 0)
        evidence += abs(weight)
        if signal.genres and weight:
            contribution = weight / len(signal.genres)
            for genre in sorted(set(signal.genres)):
                affinity[genre] += contribution

    norm = sqrt(sum(value * value for value in affinity.values()))
    normalized = tuple(
        (genre, round(value / norm if norm else 0.0, 8))
        for genre, value in sorted(affinity.items())
    )
    # Confidence is evidence-based, bounded, and deliberately not a probability of liking.
    confidence = round(evidence / (evidence + 5.0), 8) if evidence else 0.0
    return TasteFeatures(
        version=FEATURE_VERSION,
        event_count=len(active),
        positive_count=positive,
        negative_count=negative,
        evidence_weight=round(evidence, 8),
        confidence=confidence,
        genre_affinity=normalized,
    )


def offline_taste_features(signals: Iterable[TasteSignal]) -> TasteFeatures:
    return build_taste_features(signals)


def serving_taste_features(signals: Iterable[TasteSignal]) -> TasteFeatures:
    return build_taste_features(signals)
