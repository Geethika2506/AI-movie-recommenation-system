from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name cannot be blank")
        return value


class ProfileOut(ORMModel):
    id: int
    name: str
    created_at: datetime
    recommendation_version: int = 0


class MovieOut(ORMModel):
    id: int
    title: str
    year: int | None
    genres: list[str]
    tmdb_id: int | None
    overview: str | None
    poster_path: str | None
    runtime: int | None
    language: str | None
    cast: list[str]
    director: str | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def poster_url(self) -> str | None:
        if not self.poster_path:
            return None
        if self.poster_path.startswith(("https://", "http://")):
            return self.poster_path
        path = self.poster_path if self.poster_path.startswith("/") else f"/{self.poster_path}"
        return f"https://image.tmdb.org/t/p/w500{path}"


class MoviePage(BaseModel):
    items: list[MovieOut]
    page: int
    page_size: int
    total: int


class RatingIn(BaseModel):
    value: float = Field(ge=0.5, le=5.0, multiple_of=0.5)


class RatingOut(ORMModel):
    profile_id: int
    movie_id: int
    value: float
    updated_at: datetime


class InteractionIn(BaseModel):
    active: bool = True


class PreferenceUpdate(BaseModel):
    request_id: str = Field(min_length=8, max_length=80)
    action: Literal["liked", "disliked", "skip", "watchlist"]


class PreferenceUndo(BaseModel):
    action_id: int


class PreferenceStateOut(ORMModel):
    profile_id: int
    movie_id: int
    watched: bool
    preference: Literal["liked", "disliked"] | None
    watchlist: bool
    updated_at: datetime
    action_id: int | None = None
    recommendation_version: int


class RecommendationOut(BaseModel):
    movie: MovieOut
    score: float
    reason: str
    reasons: list[str] = []
    model_version: str = "local-hybrid-v1"


class GroupRequest(BaseModel):
    profile_ids: list[int] = Field(min_length=2, max_length=20)
    limit: int = Field(default=20, ge=1, le=100)
    include_watched: bool = False

    @field_validator("profile_ids")
    @classmethod
    def unique_profiles(cls, value: list[int]) -> list[int]:
        if len(set(value)) != len(value):
            raise ValueError("profile_ids must be unique")
        return value


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    profile_id: int

    @field_validator("name")
    @classmethod
    def clean_group_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name cannot be blank")
        return value


class GroupMemberOut(BaseModel):
    profile_id: int
    name: str
    role: str
    feedback_count: int
    needs_onboarding: bool


class GroupOut(BaseModel):
    id: int
    name: str
    owner_profile_id: int
    recommendation_version: int
    members: list[GroupMemberOut]


class InviteCreate(BaseModel):
    profile_id: int
    expires_in_hours: int = Field(default=48, ge=1, le=168)


class InviteOut(BaseModel):
    id: int
    token: str
    invite_url: str
    expires_at: datetime


class InviteJoin(BaseModel):
    profile_id: int


class GroupRankingRequest(BaseModel):
    requester_profile_id: int
    strategy: Literal["balanced", "everyone_matters", "custom"] = "balanced"
    mean_weight: float | None = Field(default=None, ge=0, le=1)
    include_watched: bool = False
    genres: list[str] = []
    language: str | None = Field(default=None, max_length=20)
    max_runtime: int | None = Field(default=None, ge=1, le=600)
    limit: int = Field(default=20, ge=1, le=100)


class ModelRunOut(ORMModel):
    model_name: str
    version: str
    dataset_fingerprint: str
    parameters: dict[str, object]
    metrics: dict[str, object]
    created_at: datetime


class SystemMetadataOut(BaseModel):
    active_model: str
    dataset: str
    movie_count: int
    rating_count: int
    last_trained: datetime | None
    read_only: bool
    tmdb_available: bool
