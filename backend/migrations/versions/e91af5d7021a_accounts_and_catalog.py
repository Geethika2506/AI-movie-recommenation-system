"""accounts and catalog foundation

Revision ID: e91af5d7021a
Revises: d8b21c44aa10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e91af5d7021a"
down_revision: str | None = "d8b21c44aa10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(80), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("country", sa.String(2)),
        sa.Column("streaming_provider_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("username"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"], unique=True)

    with op.batch_alter_table("profiles") as batch:
        batch.add_column(sa.Column("user_id", sa.Integer()))
        batch.create_foreign_key(
            "fk_profiles_user_id_users", "users", ["user_id"], ["id"], ondelete="CASCADE"
        )
        batch.create_index("ix_profiles_user_id", ["user_id"])
        batch.create_unique_constraint("uq_profile_user_name", ["user_id", "name"])
    with op.batch_alter_table("movies") as batch:
        batch.add_column(sa.Column("vote_average", sa.Float()))
        batch.add_column(sa.Column("keywords", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("ingested_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("updated_at", sa.DateTime(timezone=True)))
    op.execute(
        sa.text("UPDATE movies SET ingested_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP")
    )
    with op.batch_alter_table("preference_events") as batch:
        batch.add_column(
            sa.Column("origin", sa.String(30), nullable=False, server_default="organic")
        )
        batch.add_column(sa.Column("recommendation_request_id", sa.String(80)))
        batch.add_column(sa.Column("impression_id", sa.String(80)))
        batch.create_index(
            "ix_preference_events_recommendation_request_id", ["recommendation_request_id"]
        )
        batch.create_index("ix_preference_events_impression_id", ["impression_id"])

    op.create_table(
        "streaming_availability",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "movie_id",
            sa.Integer(),
            sa.ForeignKey("movies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=False),
        sa.Column("provider_name", sa.String(100), nullable=False),
        sa.Column("access_type", sa.String(20), nullable=False),
        sa.Column("source", sa.String(30), nullable=False, server_default="tmdb_justwatch"),
        sa.Column("source_url", sa.String(1000)),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "movie_id",
            "country",
            "provider_id",
            "access_type",
            name="uq_streaming_movie_country_provider_access",
        ),
    )
    op.create_index(
        "ix_streaming_country_provider",
        "streaming_availability",
        ["country", "provider_id"],
    )


def downgrade() -> None:
    op.drop_table("streaming_availability")
    with op.batch_alter_table("preference_events") as batch:
        batch.drop_index("ix_preference_events_impression_id")
        batch.drop_index("ix_preference_events_recommendation_request_id")
        batch.drop_column("impression_id")
        batch.drop_column("recommendation_request_id")
        batch.drop_column("origin")
    with op.batch_alter_table("movies") as batch:
        batch.drop_column("updated_at")
        batch.drop_column("ingested_at")
        batch.drop_column("keywords")
        batch.drop_column("vote_average")
    with op.batch_alter_table("profiles") as batch:
        batch.drop_constraint("uq_profile_user_name", type_="unique")
        batch.drop_index("ix_profiles_user_id")
        batch.drop_constraint("fk_profiles_user_id_users", type_="foreignkey")
        batch.drop_column("user_id")
    op.drop_table("refresh_tokens")
    op.drop_table("users")
