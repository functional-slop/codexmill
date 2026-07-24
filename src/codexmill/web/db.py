"""SQLAlchemy engine/session foundation (ADR 0026).

One abstraction over two engines, chosen by ``CODEXMILL_DATABASE_URL``: SQLite (the zero-config
default, a single file) or Postgres (``postgresql+psycopg://…``, for scale). SQLite connections get
WAL + a busy timeout + foreign-key enforcement. Callers pass an explicit URL/path (tests, per-store
isolation) or fall back to the resolved default."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def default_sqlite_path() -> Path:
    base = os.environ.get("CODEXMILL_CONFIG_DIR")
    root = Path(base) if base else Path.home() / ".local" / "state" / "codexmill"
    return root / "bibles.db"


def url_for_path(path: Path) -> str:
    """A SQLite URL for a filesystem path (absolute paths yield the required four slashes)."""
    return "sqlite:///" + str(path)


def resolve_url() -> str:
    """The configured database URL, or a SQLite file under the config dir by default."""
    url = os.environ.get("CODEXMILL_DATABASE_URL")
    if url:
        return url
    path = default_sqlite_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return url_for_path(path)


def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=10000")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


def make_engine(url: str) -> Engine:
    """Create an engine for ``url``, applying SQLite pragmas when relevant."""
    connect_args: dict[str, Any] = {"timeout": 10} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args, future=True, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _sqlite_pragmas)
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def upgrade_to_head(engine: Engine) -> None:
    """Apply all pending Alembic migrations to ``engine`` (ADR 0026). Called on store startup so a
    fresh database is created and an existing one is migrated to the latest schema."""
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option(
        "script_location", str(Path(__file__).resolve().parent.parent / "migrations")
    )
    cfg.attributes["engine"] = engine
    if engine.dialect.name == "postgresql":
        # Serialize concurrent first-boot migrations (multiple workers/replicas) so they can't race
        # creating alembic_version / tables. The lock is held on a separate session for the upgrade.
        with engine.connect() as lock:
            lock.exec_driver_sql("SELECT pg_advisory_lock(918273645)")
            try:
                command.upgrade(cfg, "head")
            finally:
                lock.exec_driver_sql("SELECT pg_advisory_unlock(918273645)")
    else:
        command.upgrade(cfg, "head")
