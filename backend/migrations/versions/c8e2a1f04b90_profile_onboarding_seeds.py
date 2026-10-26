"""Profile onboarding genre seeds

Revision ID: c8e2a1f04b90
Revises: b71ef0ca428d
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8e2a1f04b90"
down_revision: str | None = "b71ef0ca428d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "profiles",
        sa.Column("seed_preferred_genres", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "profiles",
        sa.Column("seed_disliked_genres", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("profiles", "seed_disliked_genres")
    op.drop_column("profiles", "seed_preferred_genres")
