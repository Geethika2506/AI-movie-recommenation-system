"""Versioned recommendation event contracts shared by serving and training."""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

EVENT_SCHEMA_VERSION = "1.0.0"


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecommendationContext(StrEnum):
    PERSONAL = "personal"
    GROUP_SESSION = "group_session"
    TRENDING = "trending"


class InteractionOrigin(StrEnum):
    ORGANIC = "organic"
    RECOMMENDATION = "recommendation"
    BANDIT_POLICY = "bandit_policy"


class AttributionStatus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    PENDING = "pending"
    ATTRIBUTED = "attributed"
    EXPIRED = "expired"


class VersionSet(ContractModel):
    dataset: str
    feature: str
    model: str
    policy: str = "none"
    experiment: str = "control"


class CandidateAction(ContractModel):
    movie_id: int = Field(gt=0)
    source: str = Field(min_length=1, max_length=40)
    score: float
    eligible: bool = True
    ineligibility_reason: str | None = None
    selection_probability: float | None = Field(default=None, gt=0, le=1)

    @model_validator(mode="after")
    def eligibility_is_explained(self) -> "CandidateAction":
        if not self.eligible and not self.ineligibility_reason:
            raise ValueError("ineligible candidates require an ineligibility reason")
        if self.eligible and self.ineligibility_reason:
            raise ValueError("eligible candidates cannot have an ineligibility reason")
        return self


class RecommendationRequestEvent(ContractModel):
    schema_version: str = EVENT_SCHEMA_VERSION
    request_id: UUID
    context: RecommendationContext
    user_id: int | None = Field(default=None, gt=0)
    session_id: UUID | None = None
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    country: str | None = Field(default=None, min_length=2, max_length=2)
    provider_ids: list[int] = Field(default_factory=list)
    versions: VersionSet

    @model_validator(mode="after")
    def identity_matches_context(self) -> "RecommendationRequestEvent":
        if self.context == RecommendationContext.GROUP_SESSION and self.session_id is None:
            raise ValueError("group requests require session_id")
        if self.context == RecommendationContext.PERSONAL and self.user_id is None:
            raise ValueError("personal requests require user_id")
        return self


class CandidateSetEvent(ContractModel):
    schema_version: str = EVENT_SCHEMA_VERSION
    request_id: UUID
    candidates: list[CandidateAction] = Field(min_length=1)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def movie_ids_are_unique(self) -> "CandidateSetEvent":
        ids = [candidate.movie_id for candidate in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("candidate movie IDs must be unique")
        return self


class DisplayedItem(ContractModel):
    movie_id: int = Field(gt=0)
    position: int = Field(ge=0)
    selection_probability: float | None = Field(default=None, gt=0, le=1)


class ImpressionEvent(ContractModel):
    schema_version: str = EVENT_SCHEMA_VERSION
    impression_id: UUID
    request_id: UUID
    displayed: list[DisplayedItem] = Field(min_length=1)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def displayed_items_are_unique(self) -> "ImpressionEvent":
        movie_ids = [item.movie_id for item in self.displayed]
        positions = [item.position for item in self.displayed]
        if len(movie_ids) != len(set(movie_ids)) or len(positions) != len(set(positions)):
            raise ValueError("displayed movie IDs and positions must be unique")
        return self


class ActionEvent(ContractModel):
    schema_version: str = EVENT_SCHEMA_VERSION
    action_id: UUID
    user_id: int = Field(gt=0)
    movie_id: int = Field(gt=0)
    action: str = Field(min_length=1, max_length=32)
    origin: InteractionOrigin
    request_id: UUID | None = None
    impression_id: UUID | None = None
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    undone_at: datetime | None = None

    @model_validator(mode="after")
    def recommendation_actions_are_traceable(self) -> "ActionEvent":
        attributed = self.origin != InteractionOrigin.ORGANIC
        if attributed and (self.request_id is None or self.impression_id is None):
            raise ValueError("recommendation actions require request_id and impression_id")
        if not attributed and (self.request_id is not None or self.impression_id is not None):
            raise ValueError("organic actions cannot claim a recommendation decision")
        if self.undone_at and self.undone_at < self.occurred_at:
            raise ValueError("undone_at cannot precede occurred_at")
        return self


class FeedbackEvent(ContractModel):
    schema_version: str = EVENT_SCHEMA_VERSION
    feedback_id: UUID
    action_id: UUID
    request_id: UUID | None = None
    impression_id: UUID | None = None
    movie_id: int = Field(gt=0)
    reward: float | None = None
    attribution_status: AttributionStatus
    attribution_window_seconds: int = Field(ge=0)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def attributed_feedback_is_traceable(self) -> "FeedbackEvent":
        if self.attribution_status == AttributionStatus.ATTRIBUTED and (
            self.request_id is None or self.impression_id is None
        ):
            raise ValueError("attributed feedback requires request_id and impression_id")
        return self


class CorrectionEvent(ContractModel):
    schema_version: str = EVENT_SCHEMA_VERSION
    correction_id: UUID
    action_id: UUID
    user_id: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=200)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class GroupVoteEvent(ContractModel):
    schema_version: str = EVENT_SCHEMA_VERSION
    vote_id: UUID
    session_id: UUID
    participant_id: int = Field(gt=0)
    movie_id: int = Field(gt=0)
    action: str = Field(min_length=1, max_length=32)
    participant_set_version: int = Field(ge=1)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
