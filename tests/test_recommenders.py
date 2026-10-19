from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from recommender.benchmark import CatalogueScorer
from recommender.content import ContentRecommender
from recommender.data import load_and_validate, synthetic_data
from recommender.evaluation import chronological_split, evaluate_predictions, evaluate_ranking
from recommender.hybrid import Candidate, HybridRecommender
from recommender.mf import ExplicitMF, MFConfig
from recommender.popularity import BayesianPopularity


def test_popularity_content_and_split_are_deterministic() -> None:
    movies, ratings = synthetic_data()
    split = chronological_split(ratings)
    assert len(split.test) == ratings["user_id"].nunique()
    assert split.train["timestamp"].max() < split.test["timestamp"].max()

    popularity = BayesianPopularity(prior_votes=2).fit(split.train, movies["movie_id"].tolist())
    first = popularity.recommend(limit=3)
    assert first == popularity.recommend(limit=3)

    content = ContentRecommender().fit(movies)
    similar = content.similar(1, limit=2)
    assert similar[0][0] == 2
    recommendations = content.recommend({1: 5.0, 4: 1.0}, limit=3)
    assert all(movie_id not in {1, 4} for movie_id, _ in recommendations)


def test_hybrid_normalizes_filters_and_diversifies() -> None:
    hybrid = HybridRecommender(weights={"popularity": 0.5, "content": 0.5}, diversity=0.2)
    candidates = {
        "popularity": [
            Candidate(1, 100, "popularity"),
            Candidate(2, 50, "popularity"),
            Candidate(3, 25, "popularity"),
        ],
        "content": [
            Candidate(2, 0.9, "content", features=np.array([1.0, 0.0])),
            Candidate(3, 0.8, "content", features=np.array([0.0, 1.0])),
        ],
    }
    result = hybrid.combine(candidates, limit=2, exclude={1})
    assert [item.movie_id for item in result] == [2, 3]
    assert all(item.reason for item in result)
    assert hybrid.combine(candidates, allowed={99}) == []


def test_matrix_factorization_save_load_and_fold_in(tmp_path: Path) -> None:
    _, ratings = synthetic_data()
    users = ratings["user_id"].to_numpy() - 1
    items = ratings["movie_id"].to_numpy() - 1
    values = ratings["rating"].to_numpy(dtype=np.float32)
    config = MFConfig(factors=3, epochs=3, batch_size=4, patience=2)
    model = ExplicitMF.train_model(users, items, values, 3, 4, config)
    before = model._loss((users, items, values))
    assert np.isfinite(before)
    bias, vector = model.fold_in(np.array([0, 1]), np.array([5.0, 4.0]))
    assert np.isfinite(bias)
    assert vector.shape == (3,)

    path = tmp_path / "model.pt"
    model.save(path, {"fingerprint": "test"})
    loaded, metadata = ExplicitMF.load(path)
    assert metadata["fingerprint"] == "test"
    assert np.isclose(loaded._loss((users, items, values)), before)
    repeated = ExplicitMF.train_model(users, items, values, 3, 4, config)
    assert all(
        np.array_equal(value.detach().numpy(), repeated.state_dict()[name].detach().numpy())
        for name, value in model.state_dict().items()
    )

    metrics = evaluate_predictions(values, values + 0.5)
    assert np.isclose(metrics["rmse"], 0.5)
    assert np.isclose(metrics["mae"], 0.5)


def test_dataset_validation_rejects_duplicate_interactions(tmp_path: Path) -> None:
    movies, ratings = synthetic_data()
    ratings = pd.concat([ratings, ratings.iloc[[0]]], ignore_index=True)
    movies.rename(columns={"movie_id": "movieId"}).to_csv(tmp_path / "movies.csv", index=False)
    ratings.rename(columns={"user_id": "userId", "movie_id": "movieId"}).to_csv(
        tmp_path / "ratings.csv", index=False
    )
    movies[["movie_id"]].rename(columns={"movie_id": "movieId"}).assign(imdbId=1, tmdbId=1).to_csv(
        tmp_path / "links.csv", index=False
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_and_validate(tmp_path)


def test_ranking_metrics_are_hand_calculated_and_validation_is_excluded() -> None:
    movies, ratings = synthetic_data()
    split = chronological_split(ratings)
    observed_exclusion: set[int] = set()

    def recommend(user_id: int, limit: int, exclude: set[int]) -> list[int]:
        assert user_id == 3
        observed_exclusion.update(exclude)
        return [4, 5][:limit]

    metrics = evaluate_ranking(
        recommend,
        split,
        set(movies["movie_id"]),
        k=2,
        target="test",
        positive_threshold=4.0,
    )
    assert observed_exclusion == {1, 2, 3}
    assert metrics["evaluated_users"] == 1
    assert metrics["evaluated_items"] == 1
    assert np.isclose(metrics["recall_at_k"], 1.0)
    assert np.isclose(metrics["precision_at_k"], 0.5)
    assert np.isclose(metrics["ndcg_at_k"], 1.0)
    assert np.isclose(metrics["coverage"], 2 / 6)


def test_evaluator_rejects_seen_item_leakage() -> None:
    movies, ratings = synthetic_data()
    split = chronological_split(ratings)

    with pytest.raises(ValueError, match="seen"):
        evaluate_ranking(
            lambda _user, _limit, _exclude: [3],
            split,
            set(movies["movie_id"]),
            k=1,
            target="test",
        )


def test_full_catalogue_scorer_has_deterministic_cold_fallbacks() -> None:
    movies, ratings = synthetic_data()
    split = chronological_split(ratings)
    user_ids = sorted(split.train["user_id"].unique())
    item_ids = sorted(split.train["movie_id"].unique())
    user_map = {int(value): index for index, value in enumerate(user_ids)}
    item_map = {int(value): index for index, value in enumerate(item_ids)}
    train_users = split.train["user_id"].map(user_map).to_numpy()
    train_items = split.train["movie_id"].map(item_map).to_numpy()
    train_ratings = split.train["rating"].to_numpy(dtype=np.float32)
    model = ExplicitMF.train_model(
        train_users,
        train_items,
        train_ratings,
        len(user_map),
        len(item_map),
        MFConfig(factors=2, epochs=2, batch_size=4, patience=1),
    )
    scorer = CatalogueScorer(movies, split, model, user_map, item_map)

    first = scorer.recommend("content", 999, 3, set())
    assert first == scorer.recommend("content", 999, 3, set())
    assert len(scorer.scores("mf", 999)) == len(movies)
    assert np.isfinite(scorer.scores("mf", 999)).all()
