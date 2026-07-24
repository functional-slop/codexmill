"""Series/continuity engine (ADR 0018): shared world + carried cast, book count, streaming, render.
Driven offline through the fake backend."""

from __future__ import annotations

from typing import Any

from codexmill.config import Settings
from codexmill.llm import Backend, make_backend
from codexmill.render import render_series
from codexmill.schemas import SeriesBible, SeriesSpec
from codexmill.series import build_series, build_series_iter


def _fake() -> object:
    return make_backend(
        Settings(backend="fake", base_url="", model="", api_key="", temperature=0.0)
    )


class _OrderSpy:
    """Records the schema name of each backend call, in order."""

    def __init__(self, inner: Backend) -> None:
        self.inner = inner
        self.calls: list[str] = []
        self.usage = inner.usage  # share the inner backend's tally (ADR 0021)

    def generate(self, system: str, user: str, schema: Any, model: str | None = None) -> Any:
        self.calls.append(schema.__name__)
        return self.inner.generate(system, user, schema, model)


def test_cast_is_generated_before_the_plan() -> None:
    # naming fix: the plan's book lineup must be able to use the real cast, so CharacterSet must be
    # generated before SeriesPlan (the plan stage receives the cast).
    spy = _OrderSpy(_fake())  # type: ignore[arg-type]
    build_series(SeriesSpec(genre="cozy romance", books=2, chapters_per_book=1), spy)
    assert spy.calls.index("CharacterSet") < spy.calls.index("SeriesPlan")


def test_build_series_shares_world_and_cast() -> None:
    spec = SeriesSpec(genre="cozy romance", books=3, chapters_per_book=2)
    series = build_series(spec, _fake())  # type: ignore[arg-type]

    assert len(series.books) == 3
    # continuity by construction: every book embeds the SAME shared world + cast objects
    for book in series.books:
        assert book.worldbuilding == series.worldbuilding
        assert book.characters == series.recurring_characters
    # each book honors the per-book chapter count
    assert all(len(b.outline.chapters) == 2 for b in series.books)
    # books are numbered/planned 1..N
    assert [bp.number for bp in series.plan.books] == [1, 2, 3]


def test_build_series_honors_book_count() -> None:
    series = build_series(SeriesSpec(genre="epic fantasy", books=5, chapters_per_book=1), _fake())  # type: ignore[arg-type]
    assert len(series.books) == 5
    assert len(series.plan.books) == 5


def test_series_stream_total_and_final() -> None:
    spec = SeriesSpec(genre="thriller", books=2, chapters_per_book=1)
    events = list(build_series_iter(spec, _fake()))  # type: ignore[arg-type]
    final = events[-1]
    assert isinstance(final, SeriesBible)
    progress = [e for e in events if isinstance(e, tuple)]
    # 3 series-level stages + 5 per book * 2 books = 13
    assert len(progress) == 13
    assert progress[-1][2] == 13  # total is consistent
    assert [p[1] for p in progress] == list(range(1, 14))  # indices 1..total in order


def test_render_series_shows_shared_sections_once() -> None:
    spec = SeriesSpec(genre="cozy romance", books=2, chapters_per_book=2)
    md = render_series(build_series(spec, _fake()))  # type: ignore[arg-type]
    assert "## Series Arc" in md
    assert "## Shared Worldbuilding" in md
    assert "## Recurring Cast" in md
    assert "# Book 1:" in md and "# Book 2:" in md
    # the shared world section appears once at series level, not repeated per book
    assert md.count("## Shared Worldbuilding") == 1
    assert md.count("## Worldbuilding") == 0  # per-book worldbuilding is suppressed
