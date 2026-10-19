"""owned preferences and groups

Revision ID: a842f1c9d771
Revises: f3b213bdd02d
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a842f1c9d771"
down_revision: str | None = "f3b213bdd02d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Development servers historically called metadata.create_all(). If hot reload
    # created this complete schema before Alembic ran, safely adopt it.
    inspector = sa.inspect(op.get_bind())
    expected_tables = {
        "user_movie_states",
        "preference_events",
        "groups",
        "group_members",
        "group_invites",
    }
    profile_columns = {column["name"] for column in inspector.get_columns("profiles")}
    if expected_tables.issubset(inspector.get_table_names()) and {
        "owner_key_hash",
        "recommendation_version",
        "updated_at",
    }.issubset(profile_columns):
        return

    with op.batch_alter_table("profiles") as batch:
        batch.add_column(
            sa.Column(
                "owner_key_hash",
                sa.String(length=64),
                nullable=False,
                server_default="",
            )
        )
        batch.add_column(
            sa.Column(
                "recommendation_version",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index("ix_profiles_owner_key_hash", ["owner_key_hash"])

    op.create_table(
        "user_movie_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("movie_id", sa.Integer(), nullable=False),
        sa.Column("watched", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("preference", sa.String(length=20)),
        sa.Column("watchlist", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["movie_id"], ["movies.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("profile_id", "movie_id"),
    )
    op.create_index(
        "ix_user_movie_state_profile_updated",
        "user_movie_states",
        ["profile_id", "updated_at"],
    )
    op.create_table(
        "preference_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("movie_id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=80), nullable=False),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("previous_state", sa.JSON()),
        sa.Column("new_state", sa.JSON()),
        sa.Column("undone", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["movie_id"], ["movies.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("profile_id", "request_id"),
    )
    op.create_index(
        "ix_preference_events_profile_created",
        "preference_events",
        ["profile_id", "created_at"],
    )
    op.create_table(
        "groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("owner_profile_id", sa.Integer(), nullable=False),
        sa.Column("recommendation_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_profile_id"], ["profiles.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "group_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("group_id", "profile_id"),
    )
    op.create_table(
        "group_invites",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_group_invites_token_hash", "group_invites", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_table("group_invites")
    op.drop_table("group_members")
    op.drop_table("groups")
    op.drop_table("preference_events")
    op.drop_table("user_movie_states")
    with op.batch_alter_table("profiles") as batch:
        batch.drop_index("ix_profiles_owner_key_hash")
        batch.drop_column("updated_at")
        batch.drop_column("recommendation_version")
        batch.drop_column("owner_key_hash")
