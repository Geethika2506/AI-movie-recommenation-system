"""Data-gated LightGBM ranking; never enabled on insufficient interaction data."""

from dataclasses import dataclass

import lightgbm as lgb
import pandas as pd


@dataclass(frozen=True)
class RankingDataGate:
    minimum_rows: int = 1_000
    minimum_users: int = 100
    minimum_positive_rows: int = 100

    def validate(self, frame: pd.DataFrame) -> None:
        required = {"user_id", "timestamp", "label"}
        if not required.issubset(frame.columns):
            raise ValueError(f"missing columns: {sorted(required - set(frame.columns))}")
        if len(frame) < self.minimum_rows:
            raise ValueError("insufficient rows for learning-to-rank")
        if frame["user_id"].nunique() < self.minimum_users:
            raise ValueError("insufficient users for learning-to-rank")
        if int((frame["label"] > 0).sum()) < self.minimum_positive_rows:
            raise ValueError("insufficient positive labels for learning-to-rank")


def train_ranker(
    frame: pd.DataFrame,
    feature_columns: list[str],
    gate: RankingDataGate | None = None,
) -> lgb.LGBMRanker:
    gate = gate or RankingDataGate()
    gate.validate(frame)
    ordered = frame.sort_values(["user_id", "timestamp"])
    cutoff = ordered["timestamp"].quantile(0.8)
    train = ordered[ordered["timestamp"] <= cutoff]
    validation = ordered[ordered["timestamp"] > cutoff]
    if validation.empty:
        raise ValueError("chronological validation partition is empty")
    model = lgb.LGBMRanker(
        objective="lambdarank",
        metric="ndcg",
        n_estimators=200,
        learning_rate=0.05,
        random_state=17,
    )
    model.fit(
        train[feature_columns],
        train["label"],
        group=train.groupby("user_id", sort=False).size().to_list(),
        eval_set=[(validation[feature_columns], validation["label"])],
        eval_group=[validation.groupby("user_id", sort=False).size().to_list()],
        callbacks=[lgb.early_stopping(20, verbose=False)],
    )
    return model
