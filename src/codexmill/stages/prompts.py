"""Stage 5 — writing prompts: assemble a ready-to-paste drafting prompt for each chapter.

Deterministic on purpose (ADR 0006): the prompt is composed from known pieces — the chapter's
scene beats, the character voice sheets, the rolling story-so-far, and the spec (POV, rating, words
per chapter). No LLM call, so the prompts are reproducible and cannot themselves drift. Threading
(voice + prior-chapter context) is directly assertable on the output; see tests/test_prompts.py."""

from __future__ import annotations

from codexmill.schemas import (
    ChapterBreakdowns,
    ChapterPrompt,
    CharacterSet,
    Premise,
    Spec,
    Worldbuilding,
    WritingPrompts,
)


def _voice_sheet(characters: CharacterSet) -> str:
    return "\n".join(f"- {c.name} ({c.role}): {c.voice}" for c in characters.characters)


def _world_digest(world: Worldbuilding) -> str:
    """A compact setting brief folded into every chapter prompt. Each prompt is meant to be pasted
    into a drafting AI on its own, so it must carry the world the bible built — otherwise all those
    worldbuilding tokens are wasted and the draft ignores the setting."""
    return (
        f"- History: {world.history}\n"
        f"- Geography: {world.geography}\n"
        f"- Cultures & daily life: {world.cultures}\n"
        f"- Factions & powers: {world.factions}\n"
        f"- Systems / rules (magic, tech, or the laws of this world): {world.systems}"
    )


def _words_per_chapter(spec: Spec, n_chapters: int) -> int:
    return spec.target_words // n_chapters if n_chapters else spec.target_words


def _words_per_scene(per_chapter: int, n_scenes: int) -> int:
    """A realistic per-reply target. No model drafts a whole long chapter well in one shot, so we
    guide the writer to draft one scene at a time and clamp the per-scene target to a range a single
    generation can actually deliver (~600-1500 words), rounded to a tidy number."""
    raw = per_chapter // n_scenes if n_scenes else per_chapter
    clamped = max(600, min(raw, 1500))
    return (clamped // 100) * 100


def generate(
    spec: Spec,
    premise: Premise,
    world: Worldbuilding,
    characters: CharacterSet,
    breakdowns: ChapterBreakdowns,
) -> WritingPrompts:
    voices = _voice_sheet(characters)
    world_brief = _world_digest(world)
    per_chapter = _words_per_chapter(spec, len(breakdowns.chapters))
    story_so_far: list[str] = []
    prompts: list[ChapterPrompt] = []

    for cd in breakdowns.chapters:
        prior = " ".join(story_so_far) if story_so_far else "(this is the opening chapter)"
        n_scenes = len(cd.scene_beats)
        per_scene = _words_per_scene(per_chapter, n_scenes)
        beats = "\n".join(f"{i + 1}. {b}" for i, b in enumerate(cd.scene_beats))
        prompt = (
            f"You are drafting Chapter {cd.number} of a {spec.genre} novel, written in "
            f"{spec.pov} POV.\n\n"
            f"Premise: {premise.logline}\n\n"
            f"Setting & rules (keep these consistent):\n{world_brief}\n\n"
            f"Keep these character voices exact:\n{voices}\n\n"
            f"Story so far: {prior}\n\n"
            f"Chapter {cd.number} — '{cd.title}' (beat: {cd.beat}).\n"
            f"What happens: {cd.summary}\n\n"
            f"Draft this chapter SCENE BY SCENE, in order — write ONE scene per reply as full, "
            f"finished prose (aim for about {per_scene} words); if a scene runs long, continue it "
            f"in your next reply before moving on. Don't rush to the end: give each beat its own "
            f"scene. The finished chapter should land around {per_chapter} words across these "
            f"{n_scenes} scene(s):\n{beats}\n\n"
            f"Content rating: {spec.maturity} (keep violence, language, and any romance within "
            "this level). Stay in the established voices. Vary your sentence rhythm: not every "
            "line needs a simile or a stack of adjectives — let some sentences be short and plain, "
            "and trust the reader. End the chapter on the emotional turn the final beat implies."
        )
        prompts.append(ChapterPrompt(number=cd.number, title=cd.title, prompt=prompt))
        story_so_far.append(f"Ch{cd.number}: {cd.summary}")

    return WritingPrompts(prompts=prompts)
