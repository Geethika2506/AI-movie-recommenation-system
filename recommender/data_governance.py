"""Machine-enforced data-use policies for ML feature builders."""

import hashlib
import json
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class DataUse(StrEnum):
    DISPLAY_ONLY = "display_only"
    ML_ELIGIBLE = "ml_eligible"
    PROHIBITED = "prohibited"


class FieldPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    use: DataUse
    notes: str


class SourcePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    terms_url: str
    reviewed_at: str
    license: str
    allowed_contexts: list[str]
    attribution: list[str] = Field(default_factory=list)
    fields: dict[str, FieldPolicy]


class DataPolicyManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_version: str
    sources: dict[str, SourcePolicy]

    @classmethod
    def load(cls, path: Path) -> "DataPolicyManifest":
        return cls.model_validate_json(path.read_text())

    def fingerprint(self) -> str:
        payload = self.model_dump_json(exclude_none=False)
        return hashlib.sha256(payload.encode()).hexdigest()

    def require_ml_fields(self, source: str, fields: set[str], context: str) -> None:
        policy = self.sources.get(source)
        if policy is None:
            raise ValueError(f"unregistered data source: {source}")
        if context not in policy.allowed_contexts:
            raise PermissionError(f"{source} is not approved for {context}")
        unknown = fields - set(policy.fields)
        if unknown:
            raise ValueError(f"unclassified fields for {source}: {sorted(unknown)}")
        blocked = sorted(
            field for field in fields if policy.fields[field].use != DataUse.ML_ELIGIBLE
        )
        if blocked:
            raise PermissionError(f"{source} fields are not ML-eligible: {blocked}")


def write_dataset_provenance(
    destination: Path,
    *,
    source: str,
    source_fingerprint: str,
    policy_fingerprint: str,
    transformations: list[str],
    created_at: str,
) -> None:
    payload = {
        "source": source,
        "source_fingerprint": source_fingerprint,
        "policy_fingerprint": policy_fingerprint,
        "transformations": transformations,
        "created_at": created_at,
    }
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
