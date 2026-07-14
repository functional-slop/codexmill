"""Series stage — plan the series: an overarching arc plus one plan per book (ADR 0018).

Runs AFTER the recurring cast is generated so the per-book premise hints name the real
characters (the cast is passed in); avoids the plan inventing a protagonist the cast contradicts.
"""

from __future__ import annotations

from codexmill.llm import Backend
from codexmill.schemas import CharacterSet, SeriesPlan, SeriesSpec

SYSTEM = (
    "You are a series architect for commercial fiction who plans multi-book arcs that keep readers "
    "buying the next book."
)


def generate(backend: Backend, spec: SeriesSpec, cast: CharacterSet | None = None) -> SeriesPlan:
    tropes = ", ".join(spec.tropes) if spec.tropes else "(choose 2-3 currently popular tropes)"
    seed = f"Series seed idea: {spec.series_premise_hint}\n" if spec.series_premise_hint else ""
    cast_line = ""
    if cast and cast.characters:
        roster = "; ".join(f"{c.name} ({c.role})" for c in cast.characters)
        cast_line = (
            f"Recurring cast (use these EXACT names in the book premises, do not invent a "
            f"different protagonist): {roster}\n"
        )
    user = (
        f"Genre: {spec.genre}\n"
        f"Tropes to feature: {tropes}\n"
        f"{seed}"
        f"{cast_line}"
        f"\nPlan a series of exactly {spec.books} books. Give the series a title and a "
        "one-paragraph overarching arc (the conflict the whole series resolves). Then for each "
        f"book give its number (1..{spec.books} in order), a title, its role in the arc (e.g. "
        "setup, escalation, climax), and a premise hint describing what that book is about while "
        "advancing the arc from the previous book. Escalate stakes book to book toward a finale."
    )
    return backend.generate(SYSTEM, user, SeriesPlan)
