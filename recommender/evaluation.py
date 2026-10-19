import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Split:
    train: pd.DataFrame
    validation: pd.DataFrame
    test: pd.DataFrame

    def history(self, include_validation: bool = False) -> pd.DataFrame:
        if not include_validation:
            return self.train
        return pd.concat([self.train, self.validation], ignore_index=True)


def chronological_split(
    ratings: pd.DataFrame, validation_items: int = 1, test_items: int = 1
) -> Split:
    required = {"user_id", "movie_id", "rating", "timestamp"}
    if not required.issubset(ratings.columns):
        raise ValueError(f"missing columns: {sorted(required - set(ratings.columns))}")
    ordered = ratings.sort_values(["user_id", "timestamp", "movie_id"])
    minimum = validation_items + test_items + 1
    eligible = ordered.groupby("user_id")["movie_id"].transform("size") >= minimum
    held_out_source = ordered[eligible]
    test = held_out_source.groupby("user_id", group_keys=False).tail(test_items)
    without_test = held_out_source.drop(test.index)
    validation = without_test.groupby("user_id", group_keys=False).tail(validation_items)
    train = ordered.drop(test.index.union(validation.index))
    if (
        len(train) + len(validation) + len(test) != len(ratings)
        or set(train.index) & set(validation.index)
        or set(train.index) & set(test.index)
        or set(validation.index) & set(test.index)
    ):
        raise AssertionError("split leakage detected")
    split = Split(
        train.reset_index(drop=True),
        validation.reset_index(drop=True),
        test.reset_index(drop=True),
    )
    assert_chronological(split)
    return split


def assert_chronological(split: Split) -> None:
    """Fail if any user's future interaction appears in an earlier partition."""
    train_last = split.train.groupby("user_id")["timestamp"].max()
    validation_first = split.validation.groupby("user_id")["timestamp"].min()
    validation_last = split.validation.groupby("user_id")["timestamp"].max()
    test_first = split.test.groupby("user_id")["timestamp"].min()
    common_validation = train_last.index.intersection(validation_first.index)
    common_test = validation_last.index.intersection(test_first.index)
    if (train_last.loc[common_validation] > validation_first.loc[common_validation]).any():
        raise AssertionError("training contains interactions after validation")
    if (validation_last.loc[common_test] > test_first.loc[common_test]).any():
        raise AssertionError("validation contains interactions after test")


def evaluate_ranking(
    recommend: Callable[[int, int, set[int]], list[int]],
    split: Split,
    all_movie_ids: set[int],
    genres: dict[int, set[str]] | None = None,
    popularity: dict[int, float] | None = None,
    k: int = 10,
    positive_threshold: float = 4.0,
    *,
    target: str = "test",
    exclude_validation: bool = True,
) -> dict[str, Any]:
    """Evaluate positive held-outs against the full catalogue.

    Only users with at least one positive in the selected held-out partition
    contribute to ranking metrics. Recommendations must be unique, in the
    catalogue, and absent from the user's observed history.
    """
    if target not in {"validation", "test"}:
        raise ValueError("target must be 'validation' or 'test'")
    recalls: list[float] = []
    precisions: list[float] = []
    ndcgs: list[float] = []
    latencies: list[float] = []
    all_recommended: list[int] = []
    diversity_scores: list[float] = []
    popularity_scores: list[float] = []
    history = split.history(include_validation=target == "test" and exclude_validation)
    history_by_user = history.groupby("user_id")["movie_id"].apply(set).to_dict()
    held_out = split.validation if target == "validation" else split.test
    positives = held_out[held_out["rating"] >= positive_threshold]
    test_by_user = positives.groupby("user_id")["movie_id"].apply(set).to_dict()
    for user_id in sorted(test_by_user):
        relevant = test_by_user[user_id]
        excluded = history_by_user.get(user_id, set())
        start = time.perf_counter()
        recs = recommend(int(user_id), k, excluded)
        latencies.append((time.perf_counter() - start) * 1000)
        if len(recs) != len(set(recs)):
            raise ValueError("recommender returned duplicate movie IDs")
        if set(recs) - all_movie_ids:
            raise ValueError("recommender returned IDs outside the catalogue")
        if set(recs) & excluded:
            raise ValueError("recommender returned seen movie IDs")
        hits = [movie_id for movie_id in recs if movie_id in relevant]
        recalls.append(len(hits) / len(relevant))
        precisions.append(len(hits) / max(k, 1))
        ndcgs.append(
            sum(
                1 / math.log2(index + 2)
                for index, movie_id in enumerate(recs)
                if movie_id in relevant
            )
            / sum(1 / math.log2(index + 2) for index in range(min(len(relevant), k)))
        )
        all_recommended.extend(recs)
        if genres:
            pairs = [
                1 - _jaccard(genres.get(left, set()), genres.get(right, set()))
                for i, left in enumerate(recs)
                for right in recs[i + 1 :]
            ]
            diversity_scores.append(float(np.mean(pairs)) if pairs else 0.0)
        if popularity:
            popularity_scores.extend(popularity.get(movie_id, 0.0) for movie_id in recs)
    return {
        "recall_at_k": float(np.mean(recalls)),
        "precision_at_k": float(np.mean(precisions)),
        "ndcg_at_k": float(np.mean(ndcgs)),
        "coverage": len(set(all_recommended)) / max(len(all_movie_ids), 1),
        "diversity": float(np.mean(diversity_scores)) if diversity_scores else 0.0,
        "popularity_bias": float(np.mean(popularity_scores)) if popularity_scores else 0.0,
        "latency_ms_p50": float(np.percentile(latencies, 50)) if latencies else 0.0,
        "latency_ms_p95": float(np.percentile(latencies, 95)) if latencies else 0.0,
        "evaluated_users": len(test_by_user),
        "evaluated_items": len({item for items in test_by_user.values() for item in items}),
        "relevant_held_out_items": sum(len(items) for items in test_by_user.values()),
        "catalogue_items": len(all_movie_ids),
    }


def evaluate_predictions(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    errors = actual - predicted
    return {
        "rmse": float(np.sqrt(np.mean(errors**2))),
        "mae": float(np.mean(np.abs(errors))),
    }


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0
