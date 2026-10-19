"""Leakage-safe, reproducible offline comparison for MovieLens latest-small."""

from __future__ import annotations

import json
import platform
import random
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import sklearn
import torch

from recommender.content import ContentRecommender
from recommender.data import profile_dataset
from recommender.evaluation import (
    Split,
    chronological_split,
    evaluate_predictions,
    evaluate_ranking,
)
from recommender.mf import ExplicitMF, MFConfig
from recommender.popularity import BayesianPopularity


@dataclass(frozen=True)
class BenchmarkConfig:
    k: int = 10
    positive_threshold: float = 4.0
    seed: int = 42
    factors: int = 32
    epochs: int = 30
    batch_size: int = 1024
    patience: int = 4


class CatalogueScorer:
    """Produce comparable full-catalogue score arrays for every model."""

    def __init__(
        self,
        movies: pd.DataFrame,
        split: Split,
        mf: ExplicitMF,
        user_map: dict[int, int],
        item_map: dict[int, int],
    ) -> None:
        self.movie_ids = movies["movie_id"].astype(int).to_numpy()
        self.positions = {movie_id: index for index, movie_id in enumerate(self.movie_ids)}
        self.train_by_user = {
            int(user_id): dict(zip(group["movie_id"], group["rating"], strict=True))
            for user_id, group in split.train.groupby("user_id")
        }
        self.popularity = BayesianPopularity().fit(split.train, self.movie_ids.tolist())
        assert self.popularity.scores_ is not None
        self.popularity_scores = self.popularity.scores_.reindex(self.movie_ids).to_numpy()
        self.content = ContentRecommender().fit(movies)
        self.mf = mf
        self.user_map = user_map
        self.item_map = item_map
        self._item_indices = np.array([item_map.get(int(item), -1) for item in self.movie_ids])
        self._cache: dict[tuple[str, int], np.ndarray] = {}

    def scores(self, model_name: str, user_id: int) -> np.ndarray:
        key = (model_name, user_id)
        if key in self._cache:
            return self._cache[key]
        if model_name == "popularity":
            result = self.popularity_scores.copy()
        elif model_name == "content":
            ratings = self.train_by_user.get(user_id, {})
            profile = self.content.taste_profile(ratings)
            if profile.nnz:
                result = (self.content.features_ @ profile.T).toarray().ravel()
            else:
                result = self.popularity_scores.copy()
        elif model_name == "mf":
            result = self._mf_scores(user_id)
        else:
            raise ValueError(f"unknown model: {model_name}")
        self._cache[key] = np.asarray(result, dtype=np.float64)
        return self._cache[key]

    def normalized_scores(self, model_name: str, user_id: int) -> np.ndarray:
        scores = self.scores(model_name, user_id)
        # IDs break score ties so ranking is deterministic across NumPy versions.
        order = np.lexsort((self.movie_ids, scores))
        ranks = np.empty(len(scores), dtype=np.float64)
        ranks[order] = np.arange(len(scores), dtype=np.float64)
        return ranks / max(len(scores) - 1, 1)

    def recommend(
        self,
        model_name: str,
        user_id: int,
        limit: int,
        exclude: set[int],
        weights: dict[str, float] | None = None,
    ) -> list[int]:
        if model_name == "hybrid":
            if weights is None:
                raise ValueError("hybrid weights are required")
            scores = sum(
                weight * self.normalized_scores(source, user_id)
                for source, weight in weights.items()
            )
        else:
            scores = self.scores(model_name, user_id)
        allowed = np.array([int(movie_id) not in exclude for movie_id in self.movie_ids])
        positions = np.flatnonzero(allowed)
        ordered = positions[np.lexsort((self.movie_ids[positions], -np.asarray(scores)[positions]))]
        return self.movie_ids[ordered[:limit]].astype(int).tolist()

    def _mf_scores(self, user_id: int) -> np.ndarray:
        known_items = self._item_indices >= 0
        result = np.full(len(self.movie_ids), self.mf.global_mean, dtype=np.float64)
        with torch.no_grad():
            if known_items.any():
                indices = torch.as_tensor(self._item_indices[known_items], dtype=torch.long)
                result[known_items] += self.mf.item_bias(indices).squeeze(1).detach().numpy()
            user_index = self.user_map.get(user_id)
            if user_index is not None:
                user = torch.tensor([user_index], dtype=torch.long)
                result += float(self.mf.user_bias(user).item())
                if known_items.any():
                    vector = self.mf.user_vectors(user).squeeze(0)
                    item_vectors = self.mf.item_vectors(
                        torch.as_tensor(self._item_indices[known_items], dtype=torch.long)
                    )
                    result[known_items] += (item_vectors @ vector).detach().numpy()
        return result


def run_benchmark(
    data_dir: Path,
    output_dir: Path,
    config: BenchmarkConfig | None = None,
) -> dict[str, Any]:
    """Train once, tune only on validation, then evaluate the untouched test."""
    config = config or BenchmarkConfig()
    started = time.perf_counter()
    _set_determinism(config.seed)
    ratings = pd.read_csv(data_dir / "ratings.csv")
    movies = pd.read_csv(data_dir / "movies.csv")
    metadata_path = data_dir / "dataset.json"
    metadata = (
        json.loads(metadata_path.read_text())
        if metadata_path.exists()
        else asdict(profile_dataset("latest-small", movies, ratings))
    )
    split = chronological_split(ratings)
    user_ids = sorted(int(value) for value in split.train["user_id"].unique())
    item_ids = sorted(int(value) for value in split.train["movie_id"].unique())
    user_map = {value: index for index, value in enumerate(user_ids)}
    item_map = {value: index for index, value in enumerate(item_ids)}
    mf_config = MFConfig(
        factors=config.factors,
        epochs=config.epochs,
        batch_size=config.batch_size,
        patience=config.patience,
        seed=config.seed,
    )
    train_data = _mapped_rows(split.train, user_map, item_map)
    validation_data = _mapped_rows(split.validation, user_map, item_map)
    mf = ExplicitMF.train_model(
        *train_data,
        len(user_map),
        len(item_map),
        config=mf_config,
        validation=validation_data if len(validation_data[0]) else None,
    )
    scorer = CatalogueScorer(movies, split, mf, user_map, item_map)
    catalogue = set(int(value) for value in movies["movie_id"])
    genres = {
        int(row.movie_id): set(str(row.genres).split("|")) for row in movies.itertuples(index=False)
    }
    popularity = split.train["movie_id"].value_counts(normalize=True).to_dict()

    weight_grid = [
        {"popularity": 1 / 3, "content": 1 / 3, "mf": 1 / 3},
        {"popularity": 0.25, "content": 0.25, "mf": 0.50},
        {"popularity": 0.25, "content": 0.50, "mf": 0.25},
        {"popularity": 0.50, "content": 0.25, "mf": 0.25},
    ]
    validation_trials = []
    for weights in weight_grid:
        metrics = _ranking_metrics(
            scorer, "hybrid", split, catalogue, genres, popularity, config, "validation", weights
        )
        validation_trials.append({"weights": weights, "metrics": metrics})
    selected = max(
        validation_trials,
        key=lambda trial: (
            trial["metrics"]["ndcg_at_k"],
            trial["metrics"]["recall_at_k"],
            tuple(trial["weights"].values()),
        ),
    )

    test_metrics: dict[str, dict[str, Any]] = {}
    for model_name in ("popularity", "content", "mf", "hybrid"):
        test_metrics[model_name] = _ranking_metrics(
            scorer,
            model_name,
            split,
            catalogue,
            genres,
            popularity,
            config,
            "test",
            selected["weights"] if model_name == "hybrid" else None,
        )
    actual, predicted, known_pairs = _mf_test_predictions(split.test, mf, user_map, item_map)
    test_metrics["mf"].update(evaluate_predictions(actual, predicted))
    test_metrics["mf"]["rating_predictions"] = len(actual)
    test_metrics["mf"]["known_user_item_predictions"] = known_pairs

    created_at = datetime.now(UTC).isoformat()
    results: dict[str, Any] = {
        "schema_version": 1,
        "dataset": {
            "name": metadata.get("name", "latest-small"),
            "fingerprint": metadata["fingerprint"],
            "ratings": len(ratings),
            "users": int(ratings["user_id"].nunique()),
            "catalogue_items": len(movies),
        },
        "protocol": {
            "split": "per-user chronological leave-last-two-out",
            "positive_threshold": config.positive_threshold,
            "k": config.k,
            "candidate_set": "every movie in movies.csv",
            "test_seen_exclusions": "train plus validation interactions",
            "model_fit": "training interactions only",
            "hybrid_selection": "highest validation NDCG@K; test not inspected",
            "cold_start": {
                "popularity": "catalogue-wide Bayesian score; unseen items use global mean",
                "content": "unknown users fall back to popularity",
                "mf": "unknown users/items use available global, user, and item biases",
                "hybrid": "inherits component fallbacks",
            },
        },
        "config": asdict(config),
        "models": {
            "mf": {
                "config": asdict(mf_config),
                "training": mf.training_summary_,
                "train_user_mapping_size": len(user_map),
                "train_item_mapping_size": len(item_map),
            },
            "content": {
                "config": {"max_features": scorer.content.max_features},
                "vocabulary_size": len(scorer.content.vectorizer_.vocabulary_),
            },
            "hybrid": {
                "selected_weights": selected["weights"],
                "validation_trials": validation_trials,
            },
        },
        "test_metrics": test_metrics,
        "measurement": {
            "created_at_utc": created_at,
            "seed": config.seed,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scikit_learn": sklearn.__version__,
            "torch": torch.__version__,
            "device": "cpu",
            "wall_clock_seconds": time.perf_counter() - started,
            "latency_scope": "single-user scoring and ranking; warm in-process model",
        },
        "limitations": [
            "Offline historical relevance is not equivalent to user satisfaction or causality.",
            "Unobserved movies are treated as non-relevant although users may have liked them.",
            "MovieLens users and catalog behaviour may not represent production traffic.",
            "One positive threshold and one chronological holdout provide no confidence intervals.",
            (
                "Latency excludes process startup, data loading, training, network, "
                "and concurrent load."
            ),
        ],
    }
    _write_artifacts(output_dir, results, mf, user_map, item_map, scorer)
    return results


def _ranking_metrics(
    scorer: CatalogueScorer,
    model_name: str,
    split: Split,
    catalogue: set[int],
    genres: dict[int, set[str]],
    popularity: dict[int, float],
    config: BenchmarkConfig,
    target: str,
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    return evaluate_ranking(
        lambda user, limit, exclude: scorer.recommend(model_name, user, limit, exclude, weights),
        split,
        catalogue,
        genres=genres,
        popularity=popularity,
        k=config.k,
        positive_threshold=config.positive_threshold,
        target=target,
        exclude_validation=True,
    )


def _mapped_rows(
    frame: pd.DataFrame, user_map: dict[int, int], item_map: dict[int, int]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    known = frame["user_id"].isin(user_map) & frame["movie_id"].isin(item_map)
    selected = frame[known]
    return (
        selected["user_id"].map(user_map).to_numpy(dtype=np.int64),
        selected["movie_id"].map(item_map).to_numpy(dtype=np.int64),
        selected["rating"].to_numpy(dtype=np.float32),
    )


def _mf_test_predictions(
    test: pd.DataFrame,
    model: ExplicitMF,
    user_map: dict[int, int],
    item_map: dict[int, int],
) -> tuple[np.ndarray, np.ndarray, int]:
    actual = test["rating"].to_numpy(dtype=np.float64)
    predicted = np.empty(len(test), dtype=np.float64)
    known_pairs = 0
    with torch.no_grad():
        for position, row in enumerate(test.itertuples(index=False)):
            user_index = user_map.get(int(row.user_id))
            item_index = item_map.get(int(row.movie_id))
            value = model.global_mean
            if user_index is not None:
                value += float(model.user_bias(torch.tensor([user_index])).item())
            if item_index is not None:
                value += float(model.item_bias(torch.tensor([item_index])).item())
            if user_index is not None and item_index is not None:
                known_pairs += 1
                value += float(
                    (
                        model.user_vectors(torch.tensor([user_index]))
                        * model.item_vectors(torch.tensor([item_index]))
                    )
                    .sum()
                    .item()
                )
            predicted[position] = value
    return actual, predicted, known_pairs


def _set_determinism(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(1)


def _write_artifacts(
    output_dir: Path,
    results: dict[str, Any],
    mf: ExplicitMF,
    user_map: dict[int, int],
    item_map: dict[int, int],
    scorer: CatalogueScorer,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    mappings = {
        "users": {str(key): value for key, value in user_map.items()},
        "movies": {str(key): value for key, value in item_map.items()},
    }
    vocabulary = {
        token: int(index) for token, index in sorted(scorer.content.vectorizer_.vocabulary_.items())
    }
    (output_dir / "mappings.json").write_text(json.dumps(mappings, sort_keys=True))
    (output_dir / "vocabulary.json").write_text(json.dumps(vocabulary, sort_keys=True))
    (output_dir / "config.json").write_text(
        json.dumps(
            {
                "config": results["config"],
                "seed": results["measurement"]["seed"],
                "created_at_utc": results["measurement"]["created_at_utc"],
                "dataset_fingerprint": results["dataset"]["fingerprint"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    mf.save(
        output_dir / "mf-model.pt",
        {
            "mapping_file": "mappings.json",
            "config_file": "config.json",
            "dataset_fingerprint": results["dataset"]["fingerprint"],
            "created_at_utc": results["measurement"]["created_at_utc"],
            "seed": results["measurement"]["seed"],
            "training": results["models"]["mf"]["training"],
        },
    )
    results["artifacts"] = {
        "mf_model": "mf-model.pt",
        "mappings": "mappings.json",
        "vocabulary": "vocabulary.json",
        "config": "config.json",
        "report": "report.md",
        "chart": "metrics.svg",
    }
    (output_dir / "results.json").write_text(json.dumps(results, indent=2, sort_keys=True))
    (output_dir / "report.md").write_text(_markdown_report(results))
    (output_dir / "metrics.svg").write_text(_svg_chart(results))


def _markdown_report(results: dict[str, Any]) -> str:
    lines = [
        "# Recommender offline evaluation",
        "",
        f"Dataset fingerprint: `{results['dataset']['fingerprint']}`  ",
        f"Measured: {results['measurement']['created_at_utc']}  ",
        f"Protocol: {results['protocol']['split']}, full catalogue, "
        f"positive rating >= {results['protocol']['positive_threshold']}, "
        f"K={results['protocol']['k']}.",
        "",
        "| Model | Recall@K | Precision@K | NDCG@K | Users | Items | p50 ms | p95 ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for model, metrics in results["test_metrics"].items():
        lines.append(
            f"| {model} | {metrics['recall_at_k']:.4f} | "
            f"{metrics['precision_at_k']:.4f} | {metrics['ndcg_at_k']:.4f} | "
            f"{metrics['evaluated_users']} | {metrics['evaluated_items']} | "
            f"{metrics['latency_ms_p50']:.3f} | {metrics['latency_ms_p95']:.3f} |"
        )
    mf = results["test_metrics"]["mf"]
    lines.extend(
        [
            "",
            f"MF explicit-rating accuracy: RMSE {mf['rmse']:.4f}, MAE {mf['mae']:.4f} "
            f"over {mf['rating_predictions']} test ratings.",
            "",
            "Hybrid weights were selected using validation NDCG only: "
            f"`{json.dumps(results['models']['hybrid']['selected_weights'], sort_keys=True)}`.",
            "",
            "## Offline limitations",
            "",
            *[f"- {item}" for item in results["limitations"]],
            "",
        ]
    )
    return "\n".join(lines)


def _svg_chart(results: dict[str, Any]) -> str:
    models = list(results["test_metrics"])
    values = [results["test_metrics"][model]["ndcg_at_k"] for model in models]
    width, left, baseline = 640, 70, 260
    bar_width = 100
    gap = 40
    bars = []
    for index, (model, value) in enumerate(zip(models, values, strict=True)):
        x = left + index * (bar_width + gap)
        bar_height = value * 200
        y = baseline - bar_height
        bars.extend(
            [
                f'<rect x="{x}" y="{y:.2f}" width="{bar_width}" '
                f'height="{bar_height:.2f}" fill="#4f46e5"/>',
                f'<text x="{x + bar_width / 2}" y="{baseline + 22}" '
                f'text-anchor="middle">{model}</text>',
                f'<text x="{x + bar_width / 2}" y="{max(y - 7, 18):.2f}" '
                f'text-anchor="middle">{value:.3f}</text>',
            ]
        )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="320" '
        'viewBox="0 0 640 320" role="img" aria-labelledby="title desc">'
        '<title id="title">Test NDCG at K by recommender</title>'
        '<desc id="desc">Offline full-catalogue ranking comparison.</desc>'
        '<rect width="100%" height="100%" fill="white"/>'
        '<text x="320" y="24" text-anchor="middle" font-size="16">Test NDCG@K</text>'
        f'<line x1="{left - 10}" y1="{baseline}" x2="{width - 10}" y2="{baseline}" '
        'stroke="#111827"/>'
        f"{''.join(bars)}</svg>"
    )
