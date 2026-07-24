"""baseline schema: bibles, rate_events, users

Revision ID: 0001_baseline
Revises:
Create Date: 2026-07-14

The baseline creates the current schema straight from the model metadata, so it is guaranteed to
match the ORM. Every schema change after this ships as its own migration.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

import codexmill.web.models  # noqa: F401  -- populate Base.metadata
from codexmill.web.db import Base

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind())
