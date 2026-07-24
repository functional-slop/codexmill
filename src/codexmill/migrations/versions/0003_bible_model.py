"""Record which model generated each bible.

A stored token count can't be interpreted without knowing the model that spent them, which matters
as soon as different users generate on different models (the server's, or their own key). Existing
rows get "" (unknown) rather than a guess.

Existence-guarded like 0002: a fresh DB builds the column from live ORM metadata in the baseline, so
this is a no-op there and only adds it to a database created before the column existed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_bible_model"
down_revision = "0002_user_llm"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("bibles")}
    if "model" not in cols:
        op.add_column(
            "bibles", sa.Column("model", sa.String(120), nullable=False, server_default="")
        )


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("bibles")}
    if "model" in cols:
        op.drop_column("bibles", "model")
