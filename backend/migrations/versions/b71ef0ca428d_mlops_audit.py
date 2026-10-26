"""ML promotion audit trail

Revision ID: b71ef0ca428d
Revises: a34de77f016c
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b71ef0ca428d"
down_revision: str | None = "a34de77f016c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_promotions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("from_version", sa.String(100)),
        sa.Column("to_version", sa.String(100), nullable=False),
        sa.Column("decision", sa.String(30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("decided_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("model_promotions")
