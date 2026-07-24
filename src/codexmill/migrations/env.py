"""Alembic environment (ADR 0026). Runs against an engine passed programmatically by
``codexmill.web.db.upgrade_to_head`` (app + tests), or one built from the configured URL for the
Alembic CLI. Online mode only; the app has a live database, never offline SQL generation."""

from __future__ import annotations

from alembic import context

import codexmill.web.models  # noqa: F401  -- register models on Base.metadata
from codexmill.web.db import Base, make_engine, resolve_url

config = context.config
target_metadata = Base.metadata


def run_migrations() -> None:
    engine = config.attributes.get("engine")
    if engine is None:
        url = config.get_main_option("sqlalchemy.url") or resolve_url()
        engine = make_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations()
