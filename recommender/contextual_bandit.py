"""Vowpal Wabbit ADF policy with traceable propensities and atomic checkpoints."""

import json
import os
import random
import threading
from dataclasses import dataclass
from pathlib import Path

from vowpalwabbit import Workspace

POLICY_VERSION = "vw-adf-epsilon-v1"
DEFAULT_REWARDS = {
    "disliked": -1.0,
    "not_interested": -1.0,
    "liked": 1.0,
    "love": 1.5,
    "watchlist": 0.35,
    "watched": 0.0,
    "skip": 0.0,
}


@dataclass(frozen=True)
class BanditAction:
    movie_id: int
    features: dict[str, float | str]
    eligible: bool = True


@dataclass(frozen=True)
class BanditDecision:
    movie_id: int
    action_index: int
    probability: float
    probabilities: tuple[float, ...]
    example_lines: tuple[str, ...]
    policy_version: str = POLICY_VERSION


class VWPolicy:
    def __init__(
        self,
        checkpoint: Path,
        *,
        epsilon: float = 0.1,
        seed: int = 17,
    ) -> None:
        self.checkpoint = checkpoint
        self.epsilon = epsilon
        self._rng = random.Random(seed)
        self._lock = threading.Lock()
        options: dict[str, object] = {
            "cb_explore_adf": True,
            "epsilon": epsilon,
            "quiet": True,
            "random_seed": seed,
        }
        if checkpoint.exists():
            options["initial_regressor"] = str(checkpoint)
        self._workspace = Workspace(**options)

    def choose(
        self,
        context: dict[str, float | str],
        actions: list[BanditAction],
    ) -> BanditDecision:
        eligible = [action for action in actions if action.eligible]
        if not eligible:
            raise ValueError("at least one eligible action is required")
        lines = [f"shared |u {_features(context)}"]
        lines.extend(
            f"|a movie_{action.movie_id} {_features(action.features)}" for action in eligible
        )
        with self._lock:
            raw = self._workspace.predict(lines)
        probabilities = tuple(float(value) for value in raw)
        if len(probabilities) != len(eligible) or sum(probabilities) <= 0:
            raise RuntimeError("VW returned an invalid action distribution")
        total = sum(probabilities)
        probabilities = tuple(value / total for value in probabilities)
        threshold = self._rng.random()
        cumulative = 0.0
        chosen = len(probabilities) - 1
        for index, probability in enumerate(probabilities):
            cumulative += probability
            if threshold <= cumulative:
                chosen = index
                break
        return BanditDecision(
            movie_id=eligible[chosen].movie_id,
            action_index=chosen,
            probability=probabilities[chosen],
            probabilities=probabilities,
            example_lines=tuple(lines),
        )

    def learn(
        self,
        decision: BanditDecision,
        reward: float,
        *,
        feedback_key: str,
        ledger_path: Path,
    ) -> bool:
        """Apply feedback once. VW minimizes cost, so cost is negative reward."""
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        applied = set(ledger_path.read_text().splitlines()) if ledger_path.exists() else set()
        if feedback_key in applied:
            return False
        lines = list(decision.example_lines)
        action_line = lines[decision.action_index + 1]
        lines[decision.action_index + 1] = f"0:{-reward}:{decision.probability} {action_line}"
        with self._lock:
            self._workspace.learn(lines)
            self._atomic_save()
            with ledger_path.open("a") as ledger:
                ledger.write(feedback_key + "\n")
        return True

    def close(self) -> None:
        self._workspace.finish()

    def _atomic_save(self) -> None:
        self.checkpoint.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.checkpoint.with_suffix(".tmp")
        self._workspace.save(str(temporary))
        os.replace(temporary, self.checkpoint)
        metadata = {
            "policy_version": POLICY_VERSION,
            "epsilon": self.epsilon,
        }
        self.checkpoint.with_suffix(".json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n"
        )


def reward_for(feedback_type: str, mapping: dict[str, float] | None = None) -> float:
    rewards = mapping or DEFAULT_REWARDS
    if feedback_type not in rewards:
        raise ValueError(f"unsupported feedback type: {feedback_type}")
    return rewards[feedback_type]


def _features(values: dict[str, float | str]) -> str:
    rendered = []
    for name, value in sorted(values.items()):
        safe_name = _safe(str(name))
        if isinstance(value, (int, float)):
            rendered.append(f"{safe_name}:{float(value):.8g}")
        else:
            rendered.append(f"{safe_name}={_safe(str(value))}")
    return " ".join(rendered)


def _safe(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "_-" else "_" for character in value
    )
