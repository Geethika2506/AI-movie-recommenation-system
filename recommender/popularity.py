from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class BayesianPopularity:
    prior_votes: float = 20.0
    scores_: pd.Series | None = None

    def fit(
        self, ratings: pd.DataFrame, movie_ids: list[int] | None = None
    ) -> "BayesianPopularity":
        required = {"movie_id", "rating"}
        if not required.issubset(ratings.columns):
            raise ValueError(f"ratings missing columns: {sorted(required - set(ratings.columns))}")
        global_mean = float(ratings["rating"].mean()) if len(ratings) else 0.0
        grouped = ratings.groupby("movie_id")["rating"].agg(["mean", "count"])
        grouped["score"] = (grouped["count"] * grouped["mean"] + self.prior_votes * global_mean) / (
            grouped["count"] + self.prior_votes
        )
        scores = grouped["score"]
        if movie_ids is not None:
            scores = scores.reindex(movie_ids, fill_value=global_mean)
        self.scores_ = scores.sort_values(ascending=False)
        return self

    def recommend(
        self, limit: int = 20, exclude: set[int] | None = None
    ) -> list[tuple[int, float]]:
        if self.scores_ is None:
            raise RuntimeError("model is not fitted")
        excluded = exclude or set()
        return [
            (int(movie_id), float(score))
            for movie_id, score in self.scores_.items()
            if int(movie_id) not in excluded
        ][:limit]

    def predict(self, movie_ids: list[int]) -> np.ndarray:
        if self.scores_ is None:
            raise RuntimeError("model is not fitted")
        fallback = float(self.scores_.mean()) if len(self.scores_) else 0.0
        return self.scores_.reindex(movie_ids, fill_value=fallback).to_numpy(dtype=float)
