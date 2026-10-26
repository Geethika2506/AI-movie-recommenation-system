from backend.mlops import (
    RetrainingState,
    assign_experiment,
    promotion_allowed,
    should_retrain,
)


def test_experiment_assignment_is_deterministic() -> None:
    variants = {"control": 90, "candidate": 10}
    assert assign_experiment("user-1", "ranking-v1", variants) == assign_experiment(
        "user-1", "ranking-v1", variants
    )
    assert assign_experiment("user-1", "ranking-v1", variants) in variants


def test_retraining_requires_data_or_measured_degradation() -> None:
    assert should_retrain(RetrainingState(new_eligible_events=10, minimum_new_events=100)) == (
        False,
        (),
    )
    decision, reasons = should_retrain(
        RetrainingState(
            new_eligible_events=100,
            minimum_new_events=100,
            ndcg_change=-0.05,
        )
    )
    assert decision
    assert set(reasons) == {
        "sufficient_new_eligible_events",
        "measured_quality_degradation",
    }


def test_model_promotion_enforces_quality_and_reliability_guardrails() -> None:
    baseline = {"ndcg_at_k": 0.2, "latency_ms_p95": 20.0, "fallback_rate": 0.0}
    candidate = {"ndcg_at_k": 0.22, "latency_ms_p95": 22.0, "fallback_rate": 0.01}
    assert promotion_allowed(baseline, candidate) == (True, ())
    rejected, reasons = promotion_allowed(
        baseline,
        {"ndcg_at_k": 0.19, "latency_ms_p95": 40.0, "fallback_rate": 0.1},
    )
    assert not rejected
    assert len(reasons) == 3
