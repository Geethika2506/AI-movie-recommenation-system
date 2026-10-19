import json
import platform
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ArtifactManifest:
    model_name: str
    version: str
    dataset_fingerprint: str
    created_at: str
    python_version: str
    parameters: dict[str, Any]
    metrics: dict[str, float]
    files: dict[str, str]

    def write(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "manifest.json"
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True))
        return path

    @classmethod
    def create(
        cls,
        model_name: str,
        version: str,
        dataset_fingerprint: str,
        parameters: dict[str, Any],
        metrics: dict[str, float],
        files: dict[str, str],
    ) -> "ArtifactManifest":
        return cls(
            model_name=model_name,
            version=version,
            dataset_fingerprint=dataset_fingerprint,
            created_at=datetime.now(UTC).isoformat(),
            python_version=platform.python_version(),
            parameters=parameters,
            metrics=metrics,
            files=files,
        )
