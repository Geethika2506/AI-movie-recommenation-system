"""drop global unique profile names

Revision ID: d8b21c44aa10
Revises: c41e9d0a12ab
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8b21c44aa10"
down_revision: str | None = "c41e9d0a12ab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    still_global = any(
        list(constraint["column_names"]) == ["name"]
        for constraint in inspector.get_unique_constraints("profiles")
    )
    if not still_global:
        return
    op.execute(sa.text("PRAGMA foreign_keys=OFF"))
    op.execute(
        sa.text(
            """
            CREATE TABLE profiles_owner_scoped (
                id INTEGER NOT NULL PRIMARY KEY,
                name VARCHAR(80) NOT NULL,
                created_at DATETIME NOT NULL,
                owner_key_hash VARCHAR(64) DEFAULT '' NOT NULL,
                recommendation_version INTEGER DEFAULT 0 NOT NULL,
                updated_at DATETIME,
                CONSTRAINT uq_profile_owner_name UNIQUE (owner_key_hash, name)
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO profiles_owner_scoped
            (id, name, created_at, owner_key_hash, recommendation_version, updated_at)
            SELECT id, name, created_at, owner_key_hash, recommendation_version, updated_at
            FROM profiles
            """
        )
    )
    op.execute(sa.text("DROP TABLE profiles"))
    op.execute(sa.text("ALTER TABLE profiles_owner_scoped RENAME TO profiles"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_profiles_owner_key_hash ON profiles (owner_key_hash)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_profiles_name ON profiles (name)"))
    op.execute(sa.text("PRAGMA foreign_keys=ON"))


def downgrade() -> None:
    pass
