"""Persistent library of generated story bibles (ADR 0010), on SQLAlchemy (ADR 0026): SQLite by
default, Postgres via ``CODEXMILL_DATABASE_URL``. Per-owner isolation: owner is the authenticated
user's stable id (``User.id``); auth is mandatory (ADR 0024)."""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from sqlalchemy import CursorResult, func, select
from sqlalchemy import delete as sa_delete
from sqlalchemy import update as sa_update
from sqlalchemy.engine import Engine

from codexmill.schemas import SeriesBible, StoryBible
from codexmill.web.db import (
    make_engine,
    make_session_factory,
    resolve_url,
    upgrade_to_head,
    url_for_path,
)
from codexmill.web.models import Bible


@dataclass(frozen=True)
class BibleSummary:
    id: str
    owner: str
    created_at: str
    title: str
    genre: str
    tokens: int = 0  # total tokens spent generating (+ regenerating) this item (ADR 0021)
    gen_seconds: float = 0.0  # total wall-clock seconds spent generating (+ regenerating) this item
    model: str = ""  # model that produced it (most recent run); "" for pre-existing rows


# Defined at module scope so annotations don't resolve `list` to the Library.list method below.
_Summaries = list[BibleSummary]
_UsageRows = list[dict[str, Any]]


class Library:
    def __init__(self, db_path: Path | None = None) -> None:
        if db_path is not None:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            url = url_for_path(db_path)
        else:
            url = resolve_url()
        self._engine = make_engine(url)
        self._session = make_session_factory(self._engine)
        # Dialect + (for SQLite) the DB file, for the direct-connection quota gate below.
        self._is_sqlite = self._engine.dialect.name == "sqlite"
        self._sqlite_file = self._engine.url.database if self._is_sqlite else None
        upgrade_to_head(self._engine)

    @property
    def engine(self) -> Engine:
        """The shared engine, so the user store can bind to the same database."""
        return self._engine

    def rekey_owner(self, old_owner: str, new_owner: str) -> int:
        """Reassign every bible from one owner key to another. Used once when migrating pre-user-
        model ownership (the ``local`` string / a username) onto a stable user id. Returns the count
        moved."""
        stmt = sa_update(Bible).where(Bible.owner == old_owner).values(owner=new_owner)
        with self._session.begin() as s:
            return cast("CursorResult[Any]", s.execute(stmt)).rowcount

    def try_consume(self, owner: str, limit: int, window_hours: float) -> tuple[bool, int]:
        """Rate-limit gate (ADR 0022). If the owner has fewer than ``limit`` generation events in
        the trailing ``window_hours``, record one and allow (returns ``(True, used_after)``);
        otherwise deny without recording (``(False, used)``). A non-positive ``limit`` means
        unlimited and always allows without recording. Counts the attempt, so a run that later
        fails still consumes its slot. The count-then-insert is atomic per owner on both engines."""
        if limit <= 0:
            return (True, 0)
        now = datetime.now(UTC)
        cutoff = (now - timedelta(hours=window_hours)).isoformat()
        if self._is_sqlite:
            return self._consume_sqlite(owner, limit, cutoff, now.isoformat())
        return self._consume_locked(owner, limit, cutoff, now.isoformat())

    def _consume_sqlite(self, owner: str, limit: int, cutoff: str, now: str) -> tuple[bool, int]:
        # A direct connection so BEGIN IMMEDIATE takes the write lock up front: concurrent consumes
        # for the same owner serialize (busy_timeout waits) instead of both reading the same count
        # and racing past the limit. WAL is already set on the file by the engine.
        if not self._sqlite_file or self._sqlite_file == ":memory:":
            raise RuntimeError(
                "the generation quota needs a file-based database, not in-memory SQLite"
            )
        conn = sqlite3.connect(self._sqlite_file, timeout=10.0)
        try:
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT COUNT(*) FROM rate_events WHERE owner=? AND ts>=?", (owner, cutoff)
            ).fetchone()
            used = int(row[0]) if row else 0
            if used >= limit:
                conn.rollback()
                return (False, used)
            conn.execute("INSERT INTO rate_events(owner, ts) VALUES(?,?)", (owner, now))
            conn.execute("DELETE FROM rate_events WHERE owner=? AND ts<?", (owner, cutoff))
            conn.commit()
            return (True, used + 1)
        finally:
            conn.close()

    def _consume_locked(self, owner: str, limit: int, cutoff: str, now: str) -> tuple[bool, int]:
        # Postgres: a per-owner transaction-scoped advisory lock serializes count-then-insert.
        raw = self._engine.raw_connection()
        try:
            cur = raw.cursor()
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (owner,))
            cur.execute(
                "SELECT COUNT(*) FROM rate_events WHERE owner=%s AND ts>=%s", (owner, cutoff)
            )
            crow = cur.fetchone()
            used = int(crow[0]) if crow else 0
            if used >= limit:
                raw.rollback()
                return (False, used)
            cur.execute("INSERT INTO rate_events(owner, ts) VALUES(%s,%s)", (owner, now))
            cur.execute("DELETE FROM rate_events WHERE owner=%s AND ts<%s", (owner, cutoff))
            raw.commit()
            return (True, used + 1)
        finally:
            raw.close()

    def _insert(
        self,
        owner: str,
        title: str,
        genre: str,
        spec_json: str,
        blob_json: str,
        kind: str,
        tokens: int,
        gen_seconds: float,
        model: str = "",
    ) -> str:
        bid = secrets.token_hex(8)
        row = Bible(
            id=bid,
            owner=owner,
            created_at=datetime.now(UTC).isoformat(),
            title=title,
            genre=genre,
            spec_json=spec_json,
            bible_json=blob_json,
            kind=kind,
            tokens=tokens,
            gen_seconds=gen_seconds,
            model=model,
        )
        with self._session.begin() as s:
            s.add(row)
        return bid

    def _list(self, owner: str, kind: str) -> _Summaries:
        stmt = (
            select(
                Bible.id,
                Bible.owner,
                Bible.created_at,
                Bible.title,
                Bible.genre,
                Bible.tokens,
                Bible.gen_seconds,
                Bible.model,
            )
            .where(Bible.owner == owner, Bible.kind == kind)
            .order_by(Bible.created_at.desc())
        )
        with self._session() as s:
            rows = s.execute(stmt).all()
        return [
            BibleSummary(
                id=r.id,
                owner=r.owner,
                created_at=r.created_at,
                title=r.title,
                genre=r.genre,
                tokens=r.tokens,
                gen_seconds=r.gen_seconds,
                model=r.model or "",
            )
            for r in rows
        ]

    def _get_json(self, owner: str, bid: str, kind: str) -> str | None:
        stmt = select(Bible.bible_json).where(
            Bible.owner == owner, Bible.id == bid, Bible.kind == kind
        )
        with self._session() as s:
            return s.execute(stmt).scalar_one_or_none()

    def _update(
        self,
        owner: str,
        bid: str,
        title: str,
        genre: str,
        spec_json: str,
        blob_json: str,
        tokens: int,
        gen_seconds: float,
        kind: str | None,
        model: str = "",
    ) -> bool:
        where = [Bible.owner == owner, Bible.id == bid]
        if kind is not None:
            where.append(Bible.kind == kind)
        values: dict[str, Any] = {
            "title": title,
            "genre": genre,
            "spec_json": spec_json,
            "bible_json": blob_json,
            "tokens": Bible.tokens + tokens,
            "gen_seconds": Bible.gen_seconds + gen_seconds,
        }
        # A regenerate may run on a different model than the original; record the latest. An empty
        # model (caller didn't supply one) must not blank out what's already stored.
        if model:
            values["model"] = model
        stmt = sa_update(Bible).where(*where).values(**values)
        with self._session.begin() as s:
            return cast("CursorResult[Any]", s.execute(stmt)).rowcount > 0

    def save(
        self,
        owner: str,
        bible: StoryBible,
        tokens: int = 0,
        gen_seconds: float = 0.0,
        model: str = "",
    ) -> str:
        return self._insert(
            owner,
            bible.premise.logline[:120],
            bible.spec.genre,
            bible.spec.model_dump_json(),
            bible.model_dump_json(),
            "book",
            tokens,
            gen_seconds,
            model,
        )

    def list(self, owner: str) -> _Summaries:
        return self._list(owner, "book")

    def get(self, owner: str, bid: str) -> StoryBible | None:
        blob = self._get_json(owner, bid, "book")
        return StoryBible.model_validate_json(blob) if blob else None

    def save_series(
        self,
        owner: str,
        series: SeriesBible,
        tokens: int = 0,
        gen_seconds: float = 0.0,
        model: str = "",
    ) -> str:
        return self._insert(
            owner,
            series.plan.series_title[:120],
            series.spec.genre,
            series.spec.model_dump_json(),
            series.model_dump_json(),
            "series",
            tokens,
            gen_seconds,
            model,
        )

    def list_series(self, owner: str) -> _Summaries:
        return self._list(owner, "series")

    def get_series(self, owner: str, bid: str) -> SeriesBible | None:
        blob = self._get_json(owner, bid, "series")
        return SeriesBible.model_validate_json(blob) if blob else None

    def update_series(
        self,
        owner: str,
        bid: str,
        series: SeriesBible,
        tokens: int = 0,
        gen_seconds: float = 0.0,
        model: str = "",
    ) -> bool:
        """Overwrite a stored series in place (keeps id + created_at). ``tokens`` and
        ``gen_seconds`` (a regenerate's cost/time) ADD to the running totals; ``model`` records
        which model the latest run used."""
        return self._update(
            owner,
            bid,
            series.plan.series_title[:120],
            series.spec.genre,
            series.spec.model_dump_json(),
            series.model_dump_json(),
            tokens,
            gen_seconds,
            "series",
            model,
        )

    def update(
        self,
        owner: str,
        bid: str,
        bible: StoryBible,
        tokens: int = 0,
        gen_seconds: float = 0.0,
        model: str = "",
    ) -> bool:
        """Overwrite a stored bible in place (keeps id + created_at). Title/genre refresh in case
        the premise or spec changed. ``tokens`` and ``gen_seconds`` ADD to the running totals;
        ``model`` records which model the latest run used."""
        return self._update(
            owner,
            bid,
            bible.premise.logline[:120],
            bible.spec.genre,
            bible.spec.model_dump_json(),
            bible.model_dump_json(),
            tokens,
            gen_seconds,
            None,
            model,
        )

    def model_for(self, owner: str, bid: str) -> str:
        """The model stored against one item, so opening a saved bible can show what made it."""
        stmt = select(Bible.model).where(Bible.owner == owner, Bible.id == bid)
        with self._session() as s:
            return s.execute(stmt).scalar_one_or_none() or ""

    def usage_by_model(self, owner: str) -> _UsageRows:
        """This owner's spend broken down by model: what was generated with what. Answers "how many
        tokens went to which model", which a bare total can't."""
        stmt = (
            select(
                Bible.model,
                func.count().label("items"),
                func.sum(Bible.tokens).label("tokens"),
            )
            .where(Bible.owner == owner)
            .group_by(Bible.model)
            .order_by(func.sum(Bible.tokens).desc())
        )
        with self._session() as s:
            rows = s.execute(stmt).all()
        return [
            {"model": r.model or "unknown", "items": int(r.items), "tokens": int(r.tokens or 0)}
            for r in rows
        ]

    def delete(self, owner: str, bid: str, kind: str = "book") -> bool:
        stmt = sa_delete(Bible).where(Bible.owner == owner, Bible.id == bid, Bible.kind == kind)
        with self._session.begin() as s:
            return cast("CursorResult[Any]", s.execute(stmt)).rowcount > 0
