"""Stage 3 — structure: map a beat framework onto the requested number of chapters."""

from __future__ import annotations

from codexmill.llm import Backend
from codexmill.schemas import CharacterSet, Outline, Premise, Spec

SYSTEM = "You are a story architect who maps proven beat frameworks onto chapter structures."


def generate(
    backend: Backend,
    spec: Spec,
    premise: Premise,
    characters: CharacterSet,
    beats: list[str],
) -> Outline:
    cast = ", ".join(c.name for c in characters.characters)
    beat_list = "\n".join(f"- {b}" for b in beats)
    user = (
        f"Premise: {premise.logline}\n"
        f"Cast: {cast}\n"
        f"Target: {spec.chapters} chapters, POV {spec.pov}.\n\n"
        f"Distribute these structural beats across exactly {spec.chapters} chapters:\n"
        f"{beat_list}\n\n"
        "For each chapter give a number, a title, the beat it serves, and a 1-2 sentence summary "
        "of what happens. Chapters must be numbered 1..N in order."
    )
    return backend.generate(SYSTEM, user, Outline)
