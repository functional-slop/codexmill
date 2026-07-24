"""add users.llm (reserved column)

Revision ID: 0002_user_llm
Revises: 0001_baseline
Create Date: 2026-07-14

A nullable JSON column reserved for a future per-user "bring your own" AI engine. It is NOT read or
written yet — non-admins currently use only the shared server AI. Kept as a nullable column so
re-introducing that feature needs no migration.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_user_llm"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The 0001 baseline builds the schema from live ORM metadata, so a database created AFTER the
    # llm column was added already has it. Guard on existence so this is a no-op on such fresh DBs
    # and only actually adds the column to a database created before it existed.
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("users")}
    if "llm" not in cols:
        op.add_column("users", sa.Column("llm", sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("users")}
    if "llm" in cols:
        op.drop_column("users", "llm")
