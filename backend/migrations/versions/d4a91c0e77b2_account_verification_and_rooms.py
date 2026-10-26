"""account verification codes and invite rooms

Revision ID: d4a91c0e77b2
Revises: c8e2a1f04b90
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4a91c0e77b2"
down_revision: str | None = "c8e2a1f04b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_challenges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("sent_to", sa.String(320), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_email_challenges_user_id", "email_challenges", ["user_id"])
    op.create_table(
        "group_rooms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("invite_id", sa.Integer(), nullable=False),
        sa.Column("host_profile_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invite_id"], ["group_invites.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["host_profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("invite_id"),
    )
    op.create_index("ix_group_rooms_group_id", "group_rooms", ["group_id"])
    op.create_table(
        "group_room_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("room_id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["room_id"], ["group_rooms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("room_id", "profile_id"),
    )
    op.create_index("ix_group_room_members_room_id", "group_room_members", ["room_id"])
    op.create_index(
        "ix_group_room_members_profile_id", "group_room_members", ["profile_id"]
    )
    with op.batch_alter_table("group_invites") as batch:
        batch.add_column(sa.Column("created_by_profile_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_group_invites_created_by_profile_id",
            "profiles",
            ["created_by_profile_id"],
            ["id"],
            ondelete="SET NULL",
        )
    with op.batch_alter_table("group_sessions") as batch:
        batch.add_column(sa.Column("room_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_group_sessions_room_id",
            "group_rooms",
            ["room_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_group_sessions_room_id", ["room_id"])


def downgrade() -> None:
    with op.batch_alter_table("group_sessions") as batch:
        batch.drop_index("ix_group_sessions_room_id")
        batch.drop_constraint("fk_group_sessions_room_id", type_="foreignkey")
        batch.drop_column("room_id")
    with op.batch_alter_table("group_invites") as batch:
        batch.drop_constraint("fk_group_invites_created_by_profile_id", type_="foreignkey")
        batch.drop_column("created_by_profile_id")
    op.drop_index("ix_group_room_members_profile_id", table_name="group_room_members")
    op.drop_index("ix_group_room_members_room_id", table_name="group_room_members")
    op.drop_table("group_room_members")
    op.drop_index("ix_group_rooms_group_id", table_name="group_rooms")
    op.drop_table("group_rooms")
    op.drop_index("ix_email_challenges_user_id", table_name="email_challenges")
    op.drop_table("email_challenges")
