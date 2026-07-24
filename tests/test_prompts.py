"""Proof that the (deterministic) writing-prompt stage threads voice sheets + the rolling
story-so-far into every per-chapter prompt. No backend needed — the stage is pure assembly, so
we assert directly on the produced prompt strings. See ADR 0006."""

from __future__ import annotations

from codexmill.schemas import (
    ChapterBreakdowns,
    ChapterDetail,
    Character,
    CharacterSet,
    Premise,
    Spec,
    Worldbuilding,
)
from codexmill.stages import prompts

VOICE_MARKER = "VOICE_MARKER_WEATHER_METAPHORS"
CH1_SUMMARY = "CH1_SUMMARY_MARKER opening events"
CH2_SUMMARY = "CH2_SUMMARY_MARKER middle events"
WORLD_MARKER = "WORLD_MARKER_TIDAL_BASALT_CITY"


def _fixture() -> tuple[Spec, Premise, Worldbuilding, CharacterSet, ChapterBreakdowns]:
    spec = Spec(genre="cozy romance", chapters=3, pov="third-limited", target_words=30000)
    premise = Premise(
        logline="A chef saves a bakery.",
        genre="cozy romance",
        tropes=["small-town"],
        central_conflict="c",
        hook="h",
    )
    world = Worldbuilding(
        history="h",
        geography=WORLD_MARKER,
        cultures="cu",
        factions="fa",
        systems="sy",
    )
    characters = CharacterSet(
        characters=[
            Character(
                name="Cormac",
                role="supporting",
                motivation="m",
                flaw="f",
                arc="a",
                voice=VOICE_MARKER,
            )
        ]
    )
    breakdowns = ChapterBreakdowns(
        chapters=[
            ChapterDetail(
                number=1, title="One", beat="setup", summary=CH1_SUMMARY, scene_beats=["a"]
            ),
            ChapterDetail(
                number=2, title="Two", beat="rising", summary=CH2_SUMMARY, scene_beats=["b"]
            ),
            ChapterDetail(number=3, title="Three", beat="turn", summary="c3", scene_beats=["c"]),
        ]
    )
    return spec, premise, world, characters, breakdowns


def test_one_prompt_per_chapter_in_order() -> None:
    spec, premise, world, characters, breakdowns = _fixture()
    result = prompts.generate(spec, premise, world, characters, breakdowns)
    assert [p.number for p in result.prompts] == [1, 2, 3]


def test_voice_sheet_in_every_prompt() -> None:
    spec, premise, world, characters, breakdowns = _fixture()
    result = prompts.generate(spec, premise, world, characters, breakdowns)
    assert all(VOICE_MARKER in p.prompt for p in result.prompts)


def test_worldbuilding_in_every_prompt() -> None:
    # The copy-paste prompt must carry the world the bible built, or the worldbuilding tokens are
    # wasted and the drafting AI ignores the setting. Every chapter prompt gets the setting brief.
    spec, premise, world, characters, breakdowns = _fixture()
    result = prompts.generate(spec, premise, world, characters, breakdowns)
    assert all(WORLD_MARKER in p.prompt for p in result.prompts)
    assert all("Setting & rules" in p.prompt for p in result.prompts)


def _story_so_far(prompt: str) -> str:
    for line in prompt.splitlines():
        if line.startswith("Story so far:"):
            return line
    return ""


def test_rolling_story_so_far_threaded() -> None:
    spec, premise, world, characters, breakdowns = _fixture()
    result = prompts.generate(spec, premise, world, characters, breakdowns)
    by_num = {p.number: p.prompt for p in result.prompts}
    # Assert on the injected "Story so far" context specifically — a chapter's own summary also
    # appears in its "What happens" line, so we must isolate the rolling-summary channel.
    assert _story_so_far(by_num[1]) == "Story so far: (this is the opening chapter)"
    assert "CH1_SUMMARY_MARKER" in _story_so_far(by_num[2])
    assert "CH2_SUMMARY_MARKER" not in _story_so_far(by_num[2])
    assert "CH1_SUMMARY_MARKER" in _story_so_far(by_num[3])
    assert "CH2_SUMMARY_MARKER" in _story_so_far(by_num[3])


def test_prompt_carries_spec_targets() -> None:
    spec, premise, world, characters, breakdowns = _fixture()
    result = prompts.generate(spec, premise, world, characters, breakdowns)
    # 30000 words / 3 chapters = 10000 words per chapter (shown as the chapter TOTAL), POV injected.
    assert "10000 words" in result.prompts[0].prompt
    assert "third-limited" in result.prompts[0].prompt


def test_prompt_guides_scene_by_scene_with_realistic_length() -> None:
    # The flaw an AI reviewer caught: asking for ~10k words in one shot. The prompt must instruct
    # scene-by-scene drafting and give a per-reply target a model can actually hit (never 10k).
    spec, premise, world, characters, breakdowns = _fixture()
    p = prompts.generate(spec, premise, world, characters, breakdowns).prompts[0].prompt
    assert "SCENE BY SCENE" in p and "one scene per reply" in p.lower()
    # the per-reply target is clamped into the achievable band
    assert "about 1500 words" in p  # clamped from 10000/1
    assert "about 10000 words" not in p  # never a single-shot 10k ask
