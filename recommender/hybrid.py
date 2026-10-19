from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class Candidate:
    movie_id: int
    score: float
    source: str
    reason: str = ""
    features: np.ndarray | None = field(default=None, compare=False, repr=False)


class HybridRecommender:
    def __init__(
        self,
        weights: dict[str, float] | None = None,
        diversity: float = 0.15,
    ) -> None:
        self.weights = weights or {"popularity": 0.2, "content": 0.35, "mf": 0.45}
        self.diversity = diversity

    def combine(
        self,
        candidates: dict[str, list[Candidate]],
        limit: int = 20,
        exclude: set[int] | None = None,
        allowed: set[int] | None = None,
    ) -> list[Candidate]:
        excluded = exclude or set()
        totals: dict[int, float] = {}
        reasons: dict[int, list[str]] = {}
        features: dict[int, np.ndarray] = {}
        for source, values in candidates.items():
            if not values:
                continue
            raw = np.asarray([item.score for item in values])
            normalized = self._percentile(raw)
            for candidate, score in zip(values, normalized, strict=True):
                if candidate.movie_id in excluded or (
                    allowed is not None and candidate.movie_id not in allowed
                ):
                    continue
                totals[candidate.movie_id] = totals.get(candidate.movie_id, 0.0) + (
                    self.weights.get(source, 0.0) * float(score)
                )
                reasons.setdefault(candidate.movie_id, []).append(candidate.reason or source)
                if candidate.features is not None:
                    features[candidate.movie_id] = candidate.features
        pool = [
            Candidate(
                mid,
                score,
                "hybrid",
                "; ".join(dict.fromkeys(reasons[mid])),
                features.get(mid),
            )
            for mid, score in totals.items()
        ]
        return self._mmr(pool, limit)

    @staticmethod
    def _percentile(values: np.ndarray) -> np.ndarray:
        if len(values) <= 1:
            return np.ones(len(values))
        order = values.argsort().argsort()
        return order / (len(values) - 1)

    def _mmr(self, candidates: list[Candidate], limit: int) -> list[Candidate]:
        selected: list[Candidate] = []
        remaining = sorted(candidates, key=lambda item: (-item.score, item.movie_id))
        while remaining and len(selected) < limit:

            def objective(item: Candidate) -> float:
                similarities = [
                    self._cosine(item.features, chosen.features)
                    for chosen in selected
                    if item.features is not None and chosen.features is not None
                ]
                return (1 - self.diversity) * item.score - self.diversity * max(
                    similarities, default=0.0
                )

            winner = max(remaining, key=lambda item: (objective(item), -item.movie_id))
            selected.append(winner)
            remaining.remove(winner)
        return selected

    @staticmethod
    def _cosine(left: np.ndarray | None, right: np.ndarray | None) -> float:
        if left is None or right is None:
            return 0.0
        denominator = np.linalg.norm(left) * np.linalg.norm(right)
        return float(np.dot(left, right) / denominator) if denominator else 0.0
