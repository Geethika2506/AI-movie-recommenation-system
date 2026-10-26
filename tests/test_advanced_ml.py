from pathlib import Path

import pandas as pd
import pytest

from recommender.collaborative import ImplicitALSRecommender
from recommender.contextual_bandit import BanditAction, VWPolicy, reward_for


def test_implicit_als_keeps_namespaces_separate_and_falls_back_safely(
    tmp_path: Path,
) -> None:
    interactions = pd.DataFrame(
        [
            ("app", 1, 1, "love"),
            ("app", 1, 2, "liked"),
            ("app", 2, 2, "love"),
            ("app", 2, 3, "liked"),
            ("movielens", 1, 3, "love"),
            ("movielens", 1, 4, "liked"),
        ],
        columns=["user_namespace", "user_id", "movie_id", "action"],
    )
    model = ImplicitALSRecommender(factors=2, iterations=2).fit(interactions)
    assert "app:1" in model.user_map_
    assert "movielens:1" in model.user_map_
    assert model.user_map_["app:1"] != model.user_map_["movielens:1"]
    assert model.recommend("app", 999, 5) == []
    artifact = model.save(tmp_path, "test-fingerprint")
    assert artifact.model_path.exists()
    loaded = ImplicitALSRecommender.load(tmp_path)
    assert loaded.recommend("app", 999, 5) == []


def test_vw_adf_logs_real_probability_and_applies_feedback_once(tmp_path: Path) -> None:
    checkpoint = tmp_path / "policy.model"
    policy = VWPolicy(checkpoint, epsilon=0.2, seed=7)
    decision = policy.choose(
        {"confidence": 0.5, "context": "personal"},
        [
            BanditAction(1, {"similarity": 0.8}),
            BanditAction(2, {"similarity": 0.4}, eligible=False),
            BanditAction(3, {"similarity": 0.2}),
        ],
    )
    assert decision.movie_id in {1, 3}
    assert len(decision.probabilities) == 2
    assert sum(decision.probabilities) == pytest.approx(1)
    assert decision.probability == decision.probabilities[decision.action_index]
    ledger = tmp_path / "ledger.txt"
    assert policy.learn(
        decision,
        reward_for("love"),
        feedback_key="feedback-1",
        ledger_path=ledger,
    )
    assert not policy.learn(
        decision,
        reward_for("love"),
        feedback_key="feedback-1",
        ledger_path=ledger,
    )
    policy.close()
    assert checkpoint.exists()
    recovered = VWPolicy(checkpoint, epsilon=0.2, seed=7)
    recovered_decision = recovered.choose(
        {"confidence": 0.5},
        [BanditAction(1, {"similarity": 0.8}), BanditAction(3, {"similarity": 0.2})],
    )
    assert recovered_decision.movie_id in {1, 3}
    recovered.close()


def test_reward_mapping_uses_cost_sign_convention() -> None:
    assert -reward_for("love") == -1.5
    assert -reward_for("disliked") == 1.0
    assert reward_for("watched") == 0.0
    with pytest.raises(ValueError, match="unsupported"):
        reward_for("impression")
