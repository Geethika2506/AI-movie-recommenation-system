"""Off-policy estimators that fail closed when logging assumptions are unmet."""

import numpy as np
import pandas as pd


def validate_logged_bandit_data(frame: pd.DataFrame) -> None:
    required = {
        "reward",
        "logging_probability",
        "target_probability",
        "eligible_action_count",
    }
    if not required.issubset(frame.columns):
        raise ValueError(f"missing logged-policy columns: {sorted(required - set(frame.columns))}")
    if frame.empty:
        raise ValueError("off-policy data is empty")
    if frame[list(required)].isna().any().any():
        raise ValueError("off-policy data contains missing logging information")
    if not frame["logging_probability"].between(0, 1, inclusive="neither").all():
        raise ValueError("logging probabilities must be in (0, 1)")
    if not frame["target_probability"].between(0, 1, inclusive="both").all():
        raise ValueError("target probabilities must be in [0, 1]")
    if (frame["eligible_action_count"] < 1).any():
        raise ValueError("eligible action sets must be logged")


def inverse_propensity_score(frame: pd.DataFrame, max_weight: float = 100.0) -> float:
    validate_logged_bandit_data(frame)
    weights = np.minimum(
        frame["target_probability"].to_numpy() / frame["logging_probability"].to_numpy(),
        max_weight,
    )
    return float(np.mean(weights * frame["reward"].to_numpy()))


def doubly_robust_score(frame: pd.DataFrame, max_weight: float = 100.0) -> float:
    validate_logged_bandit_data(frame)
    if "predicted_reward" not in frame or frame["predicted_reward"].isna().any():
        raise ValueError("doubly robust evaluation requires predicted_reward")
    weights = np.minimum(
        frame["target_probability"].to_numpy() / frame["logging_probability"].to_numpy(),
        max_weight,
    )
    predictions = frame["predicted_reward"].to_numpy()
    return float(np.mean(predictions + weights * (frame["reward"].to_numpy() - predictions)))
