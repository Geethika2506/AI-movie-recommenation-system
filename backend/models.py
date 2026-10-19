from datetime import UTC, datetime
from enum import StrEnum

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


class Profile(Base):
    __tablename__ = "profiles"
    __table_args__ = (UniqueConstraint("owner_key_hash", "name", name="uq_profile_owner_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), index=True)
    owner_key_hash: Mapped[str] = mapped_column(String(64), index=True, default="")
    recommendation_version: Mapped[int] = mapped_column(Integer, default=0)
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
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GroupInvite(Base):
    __tablename__ = "group_invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModelRun(Base):
    __tablename__ = "model_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_name: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[str] = mapped_column(String(80), unique=True)
    dataset_fingerprint: Mapped[str] = mapped_column(String(64))
    parameters: Mapped[dict[str, object]] = mapped_column(JSON)
    metrics: Mapped[dict[str, object]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
