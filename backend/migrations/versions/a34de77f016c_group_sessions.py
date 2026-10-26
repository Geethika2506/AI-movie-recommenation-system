"""group sessions, votes, and satisfaction

Revision ID: a34de77f016c
Revises: f12c84a96b30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a34de77f016c"
down_revision: str | None = "f12c84a96b30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("group_members") as batch:
        batch.add_column(
            sa.Column(
                "consent_to_use_personal_preferences",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
    op.create_table(
        "group_sessions",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(30), nullable=False, server_default="active"),
        sa.Column("filters", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("consensus_policy", sa.String(30), nullable=False),
        sa.Column("ranking_algorithm", sa.String(30), nullable=False),
        sa.Column("participant_profile_ids", sa.JSON(), nullable=False),
        sa.Column("participant_set_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("exposure_by_member", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "selected_movie_id",
            sa.Integer(),
            sa.ForeignKey("movies.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_group_sessions_group_id", "group_sessions", ["group_id"])
    op.create_table(
        "group_votes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(80),
            sa.ForeignKey("group_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "movie_id",
            sa.Integer(),
            sa.ForeignKey("movies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("request_id", sa.String(80), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "profile_id", "movie_id"),
    )
    op.create_index("ix_group_votes_session_id", "group_votes", ["session_id"])
    op.create_index("ix_group_votes_profile_id", "group_votes", ["profile_id"])
    op.create_table(
        "group_satisfaction",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(80),
            sa.ForeignKey("group_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("satisfaction", sa.Float(), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "profile_id"),
    )
    op.create_index("ix_group_satisfaction_session_id", "group_satisfaction", ["session_id"])


def downgrade() -> None:
    op.drop_table("group_satisfaction")
    op.drop_table("group_votes")
    op.drop_table("group_sessions")
    with op.batch_alter_table("group_members") as batch:
        batch.drop_column("consent_to_use_personal_preferences")
