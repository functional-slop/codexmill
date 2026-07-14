"""Stage 2 — characters: cast with motivation, flaw, arc, and a voice sheet each."""

from __future__ import annotations

from codexmill.llm import Backend
from codexmill.schemas import CharacterSet, Premise, Spec

SYSTEM = "You are a character-focused novelist who writes distinct, consistent voices."


def generate(backend: Backend, spec: Spec, premise: Premise) -> CharacterSet:
    user = (
        f"Premise: {premise.logline}\n"
        f"Central conflict: {premise.central_conflict}\n"
        f"Genre: {spec.genre}\n\n"
        "Design the core cast (protagonist, antagonist or romantic counterpart, and 1-2 "
        "supporting). For each give: name, role, motivation, a defining flaw, their arc, and a "
        "voice sheet describing exactly how they talk and think on the page (vocabulary, rhythm, "
        "verbal tics) so they never blur together."
    )
    return backend.generate(SYSTEM, user, CharacterSet)
