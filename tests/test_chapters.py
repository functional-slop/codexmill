"""End-to-end proof of the consistency mechanism ADR 0005 requires: the chapters stage must
thread (a) the rolling summary of prior chapters and (b) the character voice sheets into EVERY
chapter prompt. We drive the real `chapters.generate` with a recording Backend (a legitimate
test double, not a reimplementation) and assert on the prompts it actually sent."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from codexmill.llm import Usage
from codexmill.schemas import (
    Chapter,
    ChapterExpansion,
    Character,
    CharacterSet,
    Outline,
    Premise,
    Spec,
)
from codexmill.stages import chapters

T = TypeVar("T", bound=BaseModel)

VOICE_MARKER = "VOICE_MARKER_CLIPPED_SENTENCES"
RECAP_MARKER = "RECAP_MARKER_STATE_CHANGED"


class RecordingBackend:
    """Records every prompt and returns a fixed ChapterExpansion whose recap is a known marker."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.usage = Usage()  # satisfy the Backend protocol (ADR 0021)

    def generate(self, system: str, user: str, schema: type[T], model: str | None = None) -> T:
        self.prompts.append(user)
        return schema.model_validate(
            {
                "summary": "s",
                "scene_beats": ["b1", "b2"],
                "recap": RECAP_MARKER,
            }
        )


def _fixture() -> tuple[Spec, Premise, CharacterSet, Outline]:
    spec = Spec(genre="cozy romance", chapters=3)
    premise = Premise(
        logline="A chef saves a bakery.",
        genre="cozy romance",
        tropes=["small-town"],
        central_conflict="tradition vs. reinvention",
        hook="hook",
    )
    characters = CharacterSet(
        characters=[
            Character(
                name="Marisol",
                role="protagonist",
                motivation="m",
                flaw="f",
                arc="a",
                voice=VOICE_MARKER,
            )
        ]
    )
    outline = Outline(
        chapters=[
            Chapter(number=1, title="One", beat="setup", summary="s1"),
            Chapter(number=2, title="Two", beat="rising", summary="s2"),
            Chapter(number=3, title="Three", beat="turn", summary="s3"),
        ]
    )
    return spec, premise, characters, outline


def test_rolling_summary_is_threaded_forward() -> None:
    spec, premise, characters, outline = _fixture()
    backend = RecordingBackend()

    result = chapters.generate(backend, spec, premise, characters, outline)

    assert [c.number for c in result.chapters] == [1, 2, 3]
    assert len(backend.prompts) == 3
    # Chapter 1 has no prior chapter, so no recap yet.
    assert RECAP_MARKER not in backend.prompts[0]
    # Chapters 2 and 3 must carry the accumulated recap of prior chapters.
    assert RECAP_MARKER in backend.prompts[1]
    assert RECAP_MARKER in backend.prompts[2]


def test_voice_sheets_in_every_prompt() -> None:
    spec, premise, characters, outline = _fixture()
    backend = RecordingBackend()

    chapters.generate(backend, spec, premise, characters, outline)

    assert backend.prompts
    assert all(VOICE_MARKER in prompt for prompt in backend.prompts)


def test_expansion_schema_shape() -> None:
    exp = ChapterExpansion(summary="x", scene_beats=["a"], recap="r")
    assert exp.scene_beats == ["a"]
