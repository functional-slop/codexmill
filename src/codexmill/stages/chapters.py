"""Stage 4 — chapters: expand each outline chapter into a detailed breakdown, ONE at a time,
threading per-character voice sheets and a rolling summary of prior chapters into every prompt.

This is the moat (ADR 0005): the competing tools either use lossy RAG or no memory at all, which
is why they drift and repeat. The threading here is a correctness requirement and is verified
end-to-end in tests/test_chapters.py."""

from __future__ import annotations

from codexmill.llm import Backend
from codexmill.schemas import (
    ChapterBreakdowns,
    ChapterDetail,
    ChapterExpansion,
    CharacterSet,
    Outline,
    Premise,
    Spec,
)

SYSTEM = "You are a novelist writing detailed, continuity-consistent chapter breakdowns."


def _voice_sheet(characters: CharacterSet) -> str:
    return "\n".join(f"- {c.name} ({c.role}): {c.voice}" for c in characters.characters)


def generate(
    backend: Backend,
    spec: Spec,
    premise: Premise,
    characters: CharacterSet,
    outline: Outline,
) -> ChapterBreakdowns:
    voices = _voice_sheet(characters)
    rolling: list[str] = []  # accumulating recaps of prior chapters
    details: list[ChapterDetail] = []

    for ch in outline.chapters:
        story_so_far = " ".join(rolling) if rolling else "(this is the first chapter)"
        user = (
            f"Premise: {premise.logline}\n\n"
            f"Character voices — keep every character consistent with these:\n{voices}\n\n"
            f"Story so far (prior chapters): {story_so_far}\n\n"
            f"Now expand Chapter {ch.number} — '{ch.title}' (beat: {ch.beat}).\n"
            f"Outline note: {ch.summary}\n\n"
            "Write a detailed breakdown: a 1-2 paragraph summary, a list of concrete scene beats, "
            "and a one-sentence recap of what materially changed by the end (for continuity)."
        )
        exp = backend.generate(SYSTEM, user, ChapterExpansion)
        details.append(
            ChapterDetail(
                number=ch.number,
                title=ch.title,
                beat=ch.beat,
                summary=exp.summary,
                scene_beats=exp.scene_beats,
            )
        )
        rolling.append(f"Ch{ch.number}: {exp.recap}")

    return ChapterBreakdowns(chapters=details)
