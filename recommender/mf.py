from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset


@dataclass(frozen=True)
class MFConfig:
    factors: int = 32
    epochs: int = 30
    batch_size: int = 1024
    learning_rate: float = 0.01
    weight_decay: float = 1e-5
    patience: int = 4
    seed: int = 42


class ExplicitMF(nn.Module):
    def __init__(self, n_users: int, n_items: int, global_mean: float, config: MFConfig) -> None:
        super().__init__()
        self.config = config
        self.global_mean = global_mean
        self.user_bias = nn.Embedding(n_users, 1)
        self.item_bias = nn.Embedding(n_items, 1)
        self.user_vectors = nn.Embedding(n_users, config.factors)
        self.item_vectors = nn.Embedding(n_items, config.factors)
        self.training_summary_: dict[str, int | float] = {}
        nn.init.zeros_(self.user_bias.weight)
        nn.init.zeros_(self.item_bias.weight)
        nn.init.normal_(self.user_vectors.weight, std=0.05)
        nn.init.normal_(self.item_vectors.weight, std=0.05)

    def forward(self, users: torch.Tensor, items: torch.Tensor) -> torch.Tensor:
        interaction = (self.user_vectors(users) * self.item_vectors(items)).sum(dim=1)
        return (
            self.global_mean
            + self.user_bias(users).squeeze(1)
            + self.item_bias(items).squeeze(1)
            + interaction
        )

    @classmethod
    def train_model(
        cls,
        users: np.ndarray,
        items: np.ndarray,
        ratings: np.ndarray,
        n_users: int,
        n_items: int,
        config: MFConfig | None = None,
        validation: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None,
    ) -> "ExplicitMF":
        config = config or MFConfig()
        torch.manual_seed(config.seed)
        model = cls(n_users, n_items, float(ratings.mean()), config)
        dataset = TensorDataset(
            torch.tensor(users, dtype=torch.long),
            torch.tensor(items, dtype=torch.long),
            torch.tensor(ratings, dtype=torch.float32),
        )
        generator = torch.Generator().manual_seed(config.seed)
        loader = DataLoader(
            dataset, batch_size=config.batch_size, shuffle=True, generator=generator
        )
        optimizer = torch.optim.AdamW(
            model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
        )
        best_loss, stale, best_state = float("inf"), 0, None
        best_epoch = 0
        epochs_ran = 0
        for epoch in range(config.epochs):
            epochs_ran = epoch + 1
            model.train()
            for batch_users, batch_items, batch_ratings in loader:
                optimizer.zero_grad()
                loss = nn.functional.mse_loss(model(batch_users, batch_items), batch_ratings)
                loss.backward()
                optimizer.step()
            val_loss = (
                model._loss(validation) if validation else model._loss((users, items, ratings))
            )
            if val_loss < best_loss - 1e-5:
                best_loss, stale = val_loss, 0
                best_epoch = epoch + 1
                best_state = {
                    key: value.detach().clone() for key, value in model.state_dict().items()
                }
            else:
                stale += 1
                if stale >= config.patience:
                    break
        if best_state:
            model.load_state_dict(best_state)
        model.training_summary_ = {
            "epochs_ran": epochs_ran,
            "best_epoch": best_epoch,
            "best_validation_mse": best_loss,
            "early_stopping_patience": config.patience,
            "validation_rows": len(validation[0]) if validation else 0,
        }
        return model.eval()

    def _loss(self, data: tuple[np.ndarray, np.ndarray, np.ndarray]) -> float:
        users, items, ratings = data
        with torch.no_grad():
            predictions = self(
                torch.tensor(users, dtype=torch.long),
                torch.tensor(items, dtype=torch.long),
            )
        return float(nn.functional.mse_loss(predictions, torch.tensor(ratings)).item())

    def fold_in(
        self, item_indices: np.ndarray, ratings: np.ndarray, regularization: float = 0.1
    ) -> tuple[float, np.ndarray]:
        """Solve a new user's bias/vector while keeping item factors fixed."""
        factors = self.item_vectors.weight.detach().numpy()[item_indices]
        item_biases = self.item_bias.weight.detach().numpy().ravel()[item_indices]
        design = np.column_stack([np.ones(len(item_indices)), factors])
        target = ratings - self.global_mean - item_biases
        penalty = regularization * np.eye(design.shape[1])
        solution = np.linalg.solve(design.T @ design + penalty, design.T @ target)
        return float(solution[0]), solution[1:]

    def save(self, path: Path, metadata: dict[str, object] | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "state_dict": self.state_dict(),
                "n_users": self.user_vectors.num_embeddings,
                "n_items": self.item_vectors.num_embeddings,
                "global_mean": self.global_mean,
                "config": asdict(self.config),
                "metadata": metadata or {},
                "training_summary": getattr(self, "training_summary_", {}),
            },
            path,
        )

    @classmethod
    def load(cls, path: Path) -> tuple["ExplicitMF", dict[str, object]]:
        payload = torch.load(path, map_location="cpu", weights_only=True)
        model = cls(
            payload["n_users"],
            payload["n_items"],
            payload["global_mean"],
            MFConfig(**payload["config"]),
        )
        model.load_state_dict(payload["state_dict"])
        model.training_summary_ = payload.get("training_summary", {})
        return model.eval(), payload["metadata"]
