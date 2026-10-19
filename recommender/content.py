from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from sklearn.feature_extraction.text import TfidfVectorizer


@dataclass
class ContentRecommender:
    max_features: int = 20_000

    def fit(self, movies: pd.DataFrame) -> "ContentRecommender":
        if not {"movie_id", "title", "genres"}.issubset(movies.columns):
            raise ValueError("movies require movie_id, title and genres")
        text = (
            movies["genres"].fillna("").str.replace("|", " ", regex=False)
            + " "
            + movies["title"].fillna("")
        )
        self.vectorizer_ = TfidfVectorizer(
            stop_words="english", ngram_range=(1, 2), max_features=self.max_features
        )
        self.features_: csr_matrix = self.vectorizer_.fit_transform(text).tocsr()
        self.movie_ids_ = movies["movie_id"].astype(int).to_numpy()
        self.positions_ = {movie_id: i for i, movie_id in enumerate(self.movie_ids_)}
        return self

    def taste_profile(self, ratings: dict[int, float]) -> csr_matrix:
        self._check_fitted()
        rows, weights = [], []
        for movie_id, rating in ratings.items():
            if movie_id in self.positions_:
                rows.append(self.positions_[movie_id])
                weights.append(rating - 3.0)
        if not rows:
            return csr_matrix((1, self.features_.shape[1]))
        profile = self.features_[rows].multiply(np.asarray(weights)[:, None]).sum(axis=0)
        return csr_matrix(profile)

    def recommend(
        self, ratings: dict[int, float], limit: int = 20, exclude: set[int] | None = None
    ) -> list[tuple[int, float]]:
        profile = self.taste_profile(ratings)
        scores = (self.features_ @ profile.T).toarray().ravel()
        excluded = set(ratings) | (exclude or set())
        order = np.argsort(-scores)
        return [
            (int(self.movie_ids_[i]), float(scores[i]))
            for i in order
            if int(self.movie_ids_[i]) not in excluded
        ][:limit]

    def similar(self, movie_id: int, limit: int = 20) -> list[tuple[int, float]]:
        self._check_fitted()
        if movie_id not in self.positions_:
            return []
        row = self.features_[self.positions_[movie_id]]
        scores = (self.features_ @ row.T).toarray().ravel()
        order = np.argsort(-scores)
        return [
            (int(self.movie_ids_[i]), float(scores[i]))
            for i in order
            if int(self.movie_ids_[i]) != movie_id
        ][:limit]

    def _check_fitted(self) -> None:
        if not hasattr(self, "features_"):
            raise RuntimeError("model is not fitted")
