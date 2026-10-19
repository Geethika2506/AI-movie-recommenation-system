"""scope profile names to owning session

Revision ID: c41e9d0a12ab
Revises: a842f1c9d771
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c41e9d0a12ab"
down_revision: str | None = "a842f1c9d771"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    uniques = inspector.get_unique_constraints("profiles")
    names = {constraint["name"] for constraint in uniques if constraint["name"]}
    indexes = {index["name"] for index in inspector.get_indexes("profiles")}
    with op.batch_alter_table("profiles") as batch:
        for constraint in uniques:
            if list(constraint["column_names"]) == ["name"] and constraint["name"]:
                batch.drop_constraint(constraint["name"], type_="unique")
        if "uq_profile_owner_name" not in names and "uq_profile_owner_name" not in indexes:
            batch.create_unique_constraint("uq_profile_owner_name", ["owner_key_hash", "name"])


def downgrade() -> None:
    with op.batch_alter_table("profiles") as batch:
        batch.drop_constraint("uq_profile_owner_name", type_="unique")
        batch.create_unique_constraint("name", ["name"])
