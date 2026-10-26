"""vector embeddings and taste profiles

Revision ID: f12c84a96b30
Revises: e91af5d7021a
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "f12c84a96b30"
down_revision: str | None = "e91af5d7021a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
    op.create_table(
        "movie_embeddings",
        sa.Column(
            "movie_id",
            sa.Integer(),
            sa.ForeignKey("movies.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column("embedding_model_version", sa.String(100), nullable=False),
        sa.Column("feature_version", sa.String(80), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "user_profile_vectors",
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("taste_vector", Vector(384), nullable=False),
        sa.Column("preferred_genres", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("disliked_genres", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("profile_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("feature_version", sa.String(80), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "recommendation_impressions",
        sa.Column("request_id", sa.String(80), primary_key=True),
        sa.Column("impression_id", sa.String(80), nullable=False, unique=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("session_id", sa.String(80)),
        sa.Column("context", sa.String(30), nullable=False),
        sa.Column("candidate_movie_ids", sa.JSON(), nullable=False),
        sa.Column("displayed_movie_ids", sa.JSON(), nullable=False),
        sa.Column("displayed_positions", sa.JSON(), nullable=False),
        sa.Column("model_version", sa.String(100), nullable=False),
        sa.Column("feature_version", sa.String(100), nullable=False),
        sa.Column("policy_version", sa.String(100), nullable=False, server_default="none"),
        sa.Column("experiment_version", sa.String(100), nullable=False, server_default="control"),
        sa.Column("selection_probabilities", sa.JSON(), nullable=False),
        sa.Column("policy_context", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_recommendation_impressions_user_id", "recommendation_impressions", ["user_id"]
    )
    op.create_index(
        "ix_recommendation_impressions_impression_id",
        "recommendation_impressions",
        ["impression_id"],
        unique=True,
    )
    op.create_index(
        "ix_recommendation_impressions_session_id",
        "recommendation_impressions",
        ["session_id"],
    )
    op.create_table(
        "recommendation_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "request_id",
            sa.String(80),
            sa.ForeignKey("recommendation_impressions.request_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "movie_id",
            sa.Integer(),
            sa.ForeignKey("movies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("feedback_type", sa.String(30), nullable=False),
        sa.Column("reward", sa.Float()),
        sa.Column(
            "action_event_id",
            sa.Integer(),
            sa.ForeignKey("preference_events.id", ondelete="SET NULL"),
            unique=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("request_id", "movie_id", "feedback_type"),
    )
    op.create_index(
        "ix_recommendation_feedback_request_id", "recommendation_feedback", ["request_id"]
    )


def downgrade() -> None:
    op.drop_table("recommendation_feedback")
    op.drop_table("recommendation_impressions")
    op.drop_table("user_profile_vectors")
    op.drop_table("movie_embeddings")
