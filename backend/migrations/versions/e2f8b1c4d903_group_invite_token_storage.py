"""store invite tokens for room link copy

Revision ID: e2f8b1c4d903
Revises: d4a91c0e77b2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2f8b1c4d903"
down_revision: str | None = "d4a91c0e77b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("group_invites") as batch:
        batch.add_column(sa.Column("invite_token", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("group_invites") as batch:
        batch.drop_column("invite_token")
