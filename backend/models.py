from datetime import UTC, datetime
from enum import StrEnum

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Movie(Base):
    __tablename__ = "movies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300), index=True)
    year: Mapped[int | None] = mapped_column(Integer, index=True)
    genres: Mapped[list[str]] = mapped_column(JSON, default=list)
    tmdb_id: Mapped[int | None] = mapped_column(Integer, index=True)
    overview: Mapped[str | None] = mapped_column(Text)
    poster_path: Mapped[str | None] = mapped_column(String(1000))
    runtime: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(20))
    cast: Mapped[list[str]] = mapped_column(JSON, default=list)
    director: Mapped[str | None] = mapped_column(String(200))
    popularity_score: Mapped[float] = mapped_column(Float, default=0.0)
    vote_average: Mapped[float | None] = mapped_column(Float)
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    streaming_availability: Mapped[list["StreamingAvailability"]] = relationship(
        cascade="all, delete-orphan"
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    country: Mapped[str | None] = mapped_column(String(2))
    streaming_provider_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class EmailChallenge(Base):
    __tablename__ = "email_challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(20))
    payload: Mapped[str] = mapped_column(Text)
    code_hash: Mapped[str] = mapped_column(String(64))
    sent_to: Mapped[str] = mapped_column(String(320))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Profile(Base):
    __tablename__ = "profiles"
    __table_args__ = (
        UniqueConstraint("owner_key_hash", "name", name="uq_profile_owner_name"),
        UniqueConstraint("user_id", "name", name="uq_profile_user_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(80), index=True)
    owner_key_hash: Mapped[str] = mapped_column(String(64), index=True, default="")
    recommendation_version: Mapped[int] = mapped_column(Integer, default=0)
    seed_preferred_genres: Mapped[list[str]] = mapped_column(JSON, default=list)
    seed_disliked_genres: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    ratings: Mapped[list["Rating"]] = relationship(cascade="all, delete-orphan")


class Rating(Base):
    __tablename__ = "ratings"
    __table_args__ = (
        UniqueConstraint("profile_id", "movie_id"),
        Index("ix_ratings_profile_updated", "profile_id", "updated_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"))
    value: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class InteractionKind(StrEnum):
    WATCHED = "watched"
    DISMISSED = "dismissed"
    WATCHLIST = "watchlist"


class Interaction(Base):
    __tablename__ = "interactions"
    __table_args__ = (UniqueConstraint("profile_id", "movie_id", "kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExplicitPreference(StrEnum):
    LIKED = "liked"
    DISLIKED = "disliked"


class UserMovieState(Base):
    __tablename__ = "user_movie_states"
    __table_args__ = (
        UniqueConstraint("profile_id", "movie_id"),
        Index("ix_user_movie_state_profile_updated", "profile_id", "updated_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"))
    watched: Mapped[bool] = mapped_column(Boolean, default=False)
    preference: Mapped[str | None] = mapped_column(String(20))
    watchlist: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PreferenceEvent(Base):
    __tablename__ = "preference_events"
    __table_args__ = (
        UniqueConstraint("profile_id", "request_id"),
        Index("ix_preference_events_profile_created", "profile_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"))
    request_id: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(30))
    origin: Mapped[str] = mapped_column(String(30), default="organic")
    recommendation_request_id: Mapped[str | None] = mapped_column(String(80), index=True)
    impression_id: Mapped[str | None] = mapped_column(String(80), index=True)
    previous_state: Mapped[dict[str, object] | None] = mapped_column(JSON)
    new_state: Mapped[dict[str, object] | None] = mapped_column(JSON)
    undone: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    owner_profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    recommendation_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GroupMember(Base):
    __tablename__ = "group_members"
    __table_args__ = (UniqueConstraint("group_id", "profile_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20), default="member")
    consent_to_use_personal_preferences: Mapped[bool] = mapped_column(Boolean, default=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GroupInvite(Base):
    __tablename__ = "group_invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_by_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("profiles.id", ondelete="SET NULL")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GroupRoom(Base):
    __tablename__ = "group_rooms"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    invite_id: Mapped[int] = mapped_column(
        ForeignKey("group_invites.id", ondelete="CASCADE"), unique=True
    )
    host_profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GroupRoomMember(Base):
    __tablename__ = "group_room_members"
    __table_args__ = (UniqueConstraint("room_id", "profile_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    room_id: Mapped[int] = mapped_column(
        ForeignKey("group_rooms.id", ondelete="CASCADE"), index=True
    )
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModelRun(Base):
    __tablename__ = "model_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_name: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[str] = mapped_column(String(80), unique=True)
    dataset_fingerprint: Mapped[str] = mapped_column(String(64))
    parameters: Mapped[dict[str, object]] = mapped_column(JSON)
    metrics: Mapped[dict[str, object]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StreamingAvailability(Base):
    __tablename__ = "streaming_availability"
    __table_args__ = (
        UniqueConstraint("movie_id", "country", "provider_id", "access_type"),
        Index("ix_streaming_country_provider", "country", "provider_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"))
    country: Mapped[str] = mapped_column(String(2))
    provider_id: Mapped[int] = mapped_column(Integer)
    provider_name: Mapped[str] = mapped_column(String(100))
    access_type: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(30), default="tmdb_justwatch")
    source_url: Mapped[str | None] = mapped_column(String(1000))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MovieEmbedding(Base):
    __tablename__ = "movie_embeddings"

    movie_id: Mapped[int] = mapped_column(
        ForeignKey("movies.id", ondelete="CASCADE"), primary_key=True
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(384))
    embedding_model_version: Mapped[str] = mapped_column(String(100))
    feature_version: Mapped[str] = mapped_column(String(80))
    source_fingerprint: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class UserProfileVector(Base):
    __tablename__ = "user_profile_vectors"

    profile_id: Mapped[int] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True
    )
    taste_vector: Mapped[list[float]] = mapped_column(Vector(384))
    preferred_genres: Mapped[list[str]] = mapped_column(JSON, default=list)
    disliked_genres: Mapped[list[str]] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    profile_version: Mapped[int] = mapped_column(Integer, default=1)
    feature_version: Mapped[str] = mapped_column(String(80))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class RecommendationImpression(Base):
    __tablename__ = "recommendation_impressions"

    request_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    impression_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    session_id: Mapped[str | None] = mapped_column(String(80), index=True)
    context: Mapped[str] = mapped_column(String(30))
    candidate_movie_ids: Mapped[list[int]] = mapped_column(JSON)
    displayed_movie_ids: Mapped[list[int]] = mapped_column(JSON)
    displayed_positions: Mapped[list[int]] = mapped_column(JSON)
    model_version: Mapped[str] = mapped_column(String(100))
    feature_version: Mapped[str] = mapped_column(String(100))
    policy_version: Mapped[str] = mapped_column(String(100), default="none")
    experiment_version: Mapped[str] = mapped_column(String(100), default="control")
    selection_probabilities: Mapped[list[float | None]] = mapped_column(JSON)
    policy_context: Mapped[dict[str, object] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RecommendationFeedback(Base):
    __tablename__ = "recommendation_feedback"
    __table_args__ = (UniqueConstraint("request_id", "movie_id", "feedback_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(
        ForeignKey("recommendation_impressions.request_id", ondelete="CASCADE"), index=True
    )
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"))
    feedback_type: Mapped[str] = mapped_column(String(30))
    reward: Mapped[float | None] = mapped_column(Float)
    action_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("preference_events.id", ondelete="SET NULL"), unique=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GroupSession(Base):
    __tablename__ = "group_sessions"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    room_id: Mapped[int | None] = mapped_column(
        ForeignKey("group_rooms.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(30), default="active")
    filters: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    consensus_policy: Mapped[str] = mapped_column(String(30), default="majority")
    ranking_algorithm: Mapped[str] = mapped_column(String(30), default="fair_consensus")
    participant_profile_ids: Mapped[list[int]] = mapped_column(JSON)
    participant_set_version: Mapped[int] = mapped_column(Integer, default=1)
    exposure_by_member: Mapped[dict[str, list[int]]] = mapped_column(JSON, default=dict)
    selected_movie_id: Mapped[int | None] = mapped_column(
        ForeignKey("movies.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GroupVote(Base):
    __tablename__ = "group_votes"
    __table_args__ = (UniqueConstraint("session_id", "profile_id", "movie_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("group_sessions.id", ondelete="CASCADE"), index=True
    )
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(String(30))
    request_id: Mapped[str] = mapped_column(String(80), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GroupSatisfaction(Base):
    __tablename__ = "group_satisfaction"
    __table_args__ = (UniqueConstraint("session_id", "profile_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("group_sessions.id", ondelete="CASCADE"), index=True
    )
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    satisfaction: Mapped[float] = mapped_column(Float)
    accepted: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModelPromotion(Base):
    __tablename__ = "model_promotions"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_name: Mapped[str] = mapped_column(String(100))
    from_version: Mapped[str | None] = mapped_column(String(100))
    to_version: Mapped[str] = mapped_column(String(100))
    decision: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text)
    metrics: Mapped[dict[str, object]] = mapped_column(JSON)
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
