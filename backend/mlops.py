"""Deterministic experiments, promotion guards, and retraining triggers."""

import hashlib
from dataclasses import dataclass


def assign_experiment(
    subject_id: str,
    experiment: str,
    variants: dict[str, int],
) -> str:
    if not variants or any(weight <= 0 for weight in variants.values()):
        raise ValueError("experiment variants require positive weights")
    total = sum(variants.values())
    digest = hashlib.sha256(f"{experiment}:{subject_id}".encode()).digest()
    bucket = int.from_bytes(digest[:8], "big") % total
    cumulative = 0
    for variant, weight in variants.items():
        cumulative += weight
        if bucket < cumulative:
            return variant
    raise AssertionError("experiment assignment did not resolve")


@dataclass(frozen=True)
class RetrainingState:
    new_eligible_events: int
    minimum_new_events: int
    ndcg_change: float | None = None
    maximum_allowed_ndcg_drop: float = 0.03
    drift_score: float | None = None
    maximum_drift_score: float = 0.2


def should_retrain(state: RetrainingState) -> tuple[bool, tuple[str, ...]]:
    reasons = []
    if state.new_eligible_events >= state.minimum_new_events:
        reasons.append("sufficient_new_eligible_events")
    if state.ndcg_change is not None and state.ndcg_change <= -state.maximum_allowed_ndcg_drop:
        reasons.append("measured_quality_degradation")
    if state.drift_score is not None and state.drift_score >= state.maximum_drift_score:
        reasons.append("feature_drift")
    return bool(reasons), tuple(reasons)


def promotion_allowed(
    baseline: dict[str, float],
    candidate: dict[str, float],
    *,
    max_latency_ratio: float = 1.25,
    max_fallback_rate: float = 0.02,
) -> tuple[bool, tuple[str, ...]]:
    reasons = []
    if candidate.get("ndcg_at_k", 0.0) <= baseline.get("ndcg_at_k", 0.0):
        reasons.append("ndcg_did_not_improve")
    baseline_latency = max(baseline.get("latency_ms_p95", 1.0), 1e-9)
    if candidate.get("latency_ms_p95", float("inf")) > baseline_latency * max_latency_ratio:
        reasons.append("latency_guardrail_failed")
    if candidate.get("fallback_rate", 0.0) > max_fallback_rate:
        reasons.append("fallback_guardrail_failed")
    return not reasons, tuple(reasons)
