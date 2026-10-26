from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    computed_field,
    field_validator,
    model_validator,
)


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


class UserRegister(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    profile_name: str = Field(min_length=1, max_length=80)


class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserOut(ORMModel):
    id: int
    username: str
    email: EmailStr
    country: str | None
    streaming_provider_ids: list[int]
    created_at: datetime


class VerificationRequest(BaseModel):
    purpose: Literal["username", "email", "password"]
    current_password: str = Field(min_length=1, max_length=128)
    username: str | None = Field(
        default=None, min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$"
    )
    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=12, max_length=128)

    @model_validator(mode="after")
    def require_target(self) -> "VerificationRequest":
        if self.purpose == "username" and not self.username:
            raise ValueError("username is required")
        if self.purpose == "email" and not self.email:
            raise ValueError("email is required")
        if self.purpose == "password" and not self.password:
            raise ValueError("password is required")
        return self


class VerificationConfirm(BaseModel):
    purpose: Literal["username", "email", "password"]
    code: str = Field(pattern=r"^\d{6}$")


class VerificationIssued(BaseModel):
    purpose: Literal["username", "email", "password"]
    sent_to: EmailStr
    expires_at: datetime
    delivery: Literal["email", "preview"]
    preview_code: str | None = None


class UserSettingsUpdate(BaseModel):
    country: str | None = Field(default=None, min_length=2, max_length=2)
    streaming_provider_ids: list[int] = Field(default_factory=list, max_length=50)

    @field_validator("country")
    @classmethod
    def normalize_country(cls, value: str | None) -> str | None:
        return value.upper() if value else None

    @field_validator("streaming_provider_ids")
    @classmethod
    def unique_providers(cls, value: list[int]) -> list[int]:
        if any(provider_id <= 0 for provider_id in value):
            raise ValueError("provider IDs must be positive")
        return list(dict.fromkeys(value))


class AuthOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    user: UserOut


class LegacyProfileClaim(BaseModel):
    profile_id: int = Field(gt=0)
    legacy_session_key: str = Field(min_length=16, max_length=500)


class AccountDelete(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class ProfileOut(ORMModel):
    id: int
    name: str
    created_at: datetime
    recommendation_version: int = 0
    seed_preferred_genres: list[str] = []
    seed_disliked_genres: list[str] = []


class OnboardingPreferencesIn(BaseModel):
    preferred_genres: list[str] = Field(default_factory=list, max_length=8)
    disliked_genres: list[str] = Field(default_factory=list, max_length=8)


class StreamingAvailabilityOut(ORMModel):
    country: str
    provider_id: int
    provider_name: str
    access_type: str
    source_url: str | None
    fetched_at: datetime
    expires_at: datetime


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
    keywords: list[str] = []
    vote_average: float | None = None
    streaming_availability: list[StreamingAvailabilityOut] = []

    @field_validator("genres", "cast", "keywords", mode="before")
    @classmethod
    def normalize_lists(cls, value: object) -> object:
        return [] if value is None else value

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
    action: Literal[
        "liked",
        "disliked",
        "love",
        "skip",
        "watchlist",
        "watched",
        "not_interested",
    ]
    origin: Literal["organic", "recommendation", "bandit_policy"] = "organic"
    recommendation_request_id: str | None = Field(default=None, max_length=80)
    impression_id: str | None = Field(default=None, max_length=80)


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
    model_version: str = "content-hybrid-v1"
    feature_version: str = "taste-v1"
    policy_version: str = "none"
    request_id: str | None = None
    impression_id: str | None = None


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


class GroupJoinOut(GroupOut):
    joined_room_id: int
    joined_room_name: str


class GroupRoomMemberOut(BaseModel):
    profile_id: int
    name: str
    host: bool


class GroupRoomOut(BaseModel):
    id: int
    group_id: int
    invite_id: int
    host_profile_id: int
    host_name: str
    name: str
    members: list[GroupRoomMemberOut]


class InviteCreate(BaseModel):
    profile_id: int
    expires_in_hours: int = Field(default=48, ge=1, le=168)


class InviteOut(BaseModel):
    id: int
    token: str
    invite_url: str
    expires_at: datetime
    room_id: int
    room_name: str


class RoomInviteLinkOut(BaseModel):
    invite_url: str
    expires_at: datetime
    room_id: int
    room_name: str


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


class GroupSessionRankingUpdate(BaseModel):
    requester_profile_id: int = Field(gt=0)
    ranking_algorithm: Literal["average", "least_misery", "fair_consensus", "nash"]


class GroupSessionCreate(BaseModel):
    requester_profile_id: int = Field(gt=0)
    room_id: int | None = Field(default=None, gt=0)
    participant_profile_ids: list[int] = Field(default_factory=list, max_length=20)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    provider_ids: list[int] = Field(default_factory=list, max_length=50)
    max_runtime: int | None = Field(default=None, ge=1, le=600)
    languages: list[str] = Field(default_factory=list, max_length=20)
    genres: list[str] = Field(default_factory=list, max_length=20)
    age_rating: str | None = Field(default=None, max_length=20)
    exclusions: list[int] = Field(default_factory=list, max_length=200)
    consensus_policy: Literal["strong", "majority", "unanimous", "veto"] = "majority"
    ranking_algorithm: Literal["average", "least_misery", "fair_consensus", "nash"] = (
        "fair_consensus"
    )


class GroupSessionOut(ORMModel):
    id: str
    group_id: int
    room_id: int | None = None
    status: str
    filters: dict[str, object]
    consensus_policy: str
    ranking_algorithm: str
    participant_profile_ids: list[int]
    participant_set_version: int
    exposure_by_member: dict[str, list[int]]
    selected_movie_id: int | None
    created_at: datetime
    finalized_at: datetime | None


class GroupVoteIn(BaseModel):
    profile_id: int = Field(gt=0)
    movie_id: int = Field(gt=0)
    action: Literal["like", "love", "dislike", "veto", "skip"]
    request_id: str = Field(min_length=8, max_length=80)


class GroupVoteOut(ORMModel):
    id: int
    session_id: str
    profile_id: int
    movie_id: int
    action: str
    request_id: str
    created_at: datetime


class GroupFinalizeIn(BaseModel):
    requester_profile_id: int = Field(gt=0)
    movie_id: int = Field(gt=0)


class GroupSatisfactionIn(BaseModel):
    profile_id: int = Field(gt=0)
    satisfaction: float = Field(ge=0, le=1)
    accepted: bool


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
