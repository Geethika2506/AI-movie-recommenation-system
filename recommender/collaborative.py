"""Implicit-feedback ALS with explicit user namespaces and safe cold fallbacks."""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from implicit.cpu.als import AlternatingLeastSquares
from scipy.sparse import csr_matrix, load_npz, save_npz

CONFIDENCE_WEIGHTS = {
    "disliked": -2.0,
    "not_interested": -2.0,
    "liked": 3.0,
    "love": 5.0,
    "watchlist": 1.0,
    "watched": 0.0,
    "skip": 0.0,
}


@dataclass(frozen=True)
class CollaborativeArtifact:
    model_path: Path
    manifest_path: Path


class ImplicitALSRecommender:
    def __init__(self, factors: int = 32, iterations: int = 20, regularization: float = 0.05):
        self.factors = factors
        self.iterations = iterations
        self.regularization = regularization

    def fit(self, interactions: pd.DataFrame) -> "ImplicitALSRecommender":
        required = {"user_namespace", "user_id", "movie_id", "action"}
        if not required.issubset(interactions.columns):
            raise ValueError(f"missing columns: {sorted(required - set(interactions.columns))}")
        frame = interactions.copy()
        frame["confidence"] = frame["action"].map(CONFIDENCE_WEIGHTS)
        frame = frame[frame["confidence"].notna() & (frame["confidence"] != 0)]
        if frame.empty:
            raise ValueError("no confidence-bearing interactions")
        frame["user_key"] = frame["user_namespace"].astype(str) + ":" + frame["user_id"].astype(str)
        self.user_keys_ = sorted(frame["user_key"].unique())
        self.movie_ids_ = sorted(int(value) for value in frame["movie_id"].unique())
        self.user_map_ = {value: index for index, value in enumerate(self.user_keys_)}
        self.item_map_ = {value: index for index, value in enumerate(self.movie_ids_)}
        rows = frame["user_key"].map(self.user_map_).to_numpy()
        columns = frame["movie_id"].map(self.item_map_).to_numpy()
        values = frame["confidence"].astype(np.float32).to_numpy()
        self.matrix_ = csr_matrix(
            (values, (rows, columns)),
            shape=(len(self.user_map_), len(self.item_map_)),
        )
        self.model_ = AlternatingLeastSquares(
            factors=self.factors,
            iterations=self.iterations,
            regularization=self.regularization,
            random_state=17,
        )
        self.model_.fit(self.matrix_, show_progress=False)
        return self

    def recommend(
        self,
        namespace: str,
        user_id: int,
        limit: int,
        exclude: set[int] | None = None,
    ) -> list[tuple[int, float]]:
        self._check_fitted()
        key = f"{namespace}:{user_id}"
        if key not in self.user_map_:
            return []
        row = self.user_map_[key]
        ids, scores = self.model_.recommend(
            row,
            self.matrix_[row],
            N=min(limit + len(exclude or set()), len(self.movie_ids_)),
            filter_already_liked_items=True,
        )
        excluded = exclude or set()
        return [
            (self.movie_ids_[int(item)], float(score))
            for item, score in zip(ids, scores, strict=True)
            if self.movie_ids_[int(item)] not in excluded
        ][:limit]

    def save(self, artifact_dir: Path, dataset_fingerprint: str) -> CollaborativeArtifact:
        self._check_fitted()
        artifact_dir.mkdir(parents=True, exist_ok=True)
        model_path = artifact_dir / "als-model.npz"
        self.model_.save(model_path)
        save_npz(artifact_dir / "interaction-matrix.npz", self.matrix_)
        manifest_path = artifact_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "version": "implicit-als-v1",
                    "dataset_fingerprint": dataset_fingerprint,
                    "factors": self.factors,
                    "iterations": self.iterations,
                    "regularization": self.regularization,
                    "user_keys": self.user_keys_,
                    "movie_ids": self.movie_ids_,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        return CollaborativeArtifact(model_path, manifest_path)

    @classmethod
    def load(cls, artifact_dir: Path) -> "ImplicitALSRecommender":
        manifest = json.loads((artifact_dir / "manifest.json").read_text())
        instance = cls(
            factors=int(manifest["factors"]),
            iterations=int(manifest["iterations"]),
            regularization=float(manifest["regularization"]),
        )
        instance.user_keys_ = list(manifest["user_keys"])
        instance.movie_ids_ = [int(value) for value in manifest["movie_ids"]]
        instance.user_map_ = {value: index for index, value in enumerate(instance.user_keys_)}
        instance.item_map_ = {value: index for index, value in enumerate(instance.movie_ids_)}
        instance.model_ = AlternatingLeastSquares.load(artifact_dir / "als-model.npz")
        instance.matrix_ = load_npz(artifact_dir / "interaction-matrix.npz").tocsr()
        return instance

    def _check_fitted(self) -> None:
        if not hasattr(self, "model_"):
            raise RuntimeError("collaborative model is not fitted")
