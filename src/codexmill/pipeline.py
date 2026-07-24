"""Orchestrates the stages into a full story bible. See docs/adr/0004."""

from __future__ import annotations

from collections.abc import Iterator

from codexmill import beats
from codexmill.llm import Backend, bind_model
from codexmill.schemas import Spec, StoryBible
from codexmill.stages import chapters as chapters_stage
from codexmill.stages import characters as characters_stage
from codexmill.stages import metadata as metadata_stage
from codexmill.stages import premise as premise_stage
from codexmill.stages import prompts as prompts_stage
from codexmill.stages import structure as structure_stage
from codexmill.stages import worldbuilding as worldbuilding_stage

# Human-facing stage labels, in run order — used for streaming progress (ADR 0011).
STAGE_LABELS = [
    "premise",
    "worldbuilding",
    "characters",
    "structure",
    "chapters",
    "writing prompts",
    "KDP metadata",
]


def build_iter(
    spec: Spec, backend: Backend, stage_models: dict[str, str] | None = None
) -> Iterator[tuple[str, int, int] | StoryBible]:
    """Run the pipeline, yielding a ``(stage_label, index, total)`` tuple as each stage starts,
    and finally the assembled ``StoryBible``. Lets the web layer stream progress; the LLM calls
    block, so callers run this in a thread (Starlette does for a sync StreamingResponse).

    ``stage_models`` optionally maps a stage label to a model, so a strong model can do
    premise/structure and a cheap/local one the bulk chapters (ADR 0005)."""
    models = stage_models or {}
    total = len(STAGE_LABELS)

    def be(stage: str) -> Backend:
        return bind_model(backend, models.get(stage))

    yield ("premise", 1, total)
    premise = premise_stage.generate(be("premise"), spec)
    yield ("worldbuilding", 2, total)
    world = worldbuilding_stage.generate(be("worldbuilding"), spec, premise)
    yield ("characters", 3, total)
    characters = characters_stage.generate(be("characters"), spec, premise, world)
    yield ("structure", 4, total)
    _framework_name, beat_list = beats.select(spec.genre, spec.framework)
    outline = structure_stage.generate(be("structure"), spec, premise, characters, beat_list)
    yield ("chapters", 5, total)
    breakdowns = chapters_stage.generate(be("chapters"), spec, premise, characters, outline)
    yield ("writing prompts", 6, total)
    writing_prompts = prompts_stage.generate(spec, premise, world, characters, breakdowns)
    yield ("KDP metadata", 7, total)
    kdp = metadata_stage.generate(be("KDP metadata"), spec, premise)
    yield StoryBible(
        spec=spec,
        premise=premise,
        worldbuilding=world,
        characters=characters,
        outline=outline,
        breakdowns=breakdowns,
        writing_prompts=writing_prompts,
        kdp=kdp,
    )


# For each stage, the stages that must be re-run WITH it to keep the bible consistent — itself
# plus every stage that (transitively) consumes its output, in run order. Derived from the stage
# signatures in build_iter: KDP is a leaf; worldbuilding now feeds the writing prompts (its setting
# brief is embedded in each one), so redoing it also redoes prompts; characters feeds structure→
# chapters→prompts; premise feeds everything. Regenerating a stage never touches an upstream stage.
_REGEN_CASCADE: dict[str, list[str]] = {
    "premise": list(STAGE_LABELS),
    "worldbuilding": ["worldbuilding", "writing prompts"],
    "characters": ["characters", "structure", "chapters", "writing prompts"],
    "structure": ["structure", "chapters", "writing prompts"],
    "chapters": ["chapters", "writing prompts"],
    "writing prompts": ["writing prompts"],
    "KDP metadata": ["KDP metadata"],
}


def regenerate(
    backend: Backend,
    existing: StoryBible,
    stage: str,
    stage_models: dict[str, str] | None = None,
) -> StoryBible:
    """Re-run one stage of an existing bible and every stage that depends on it, reusing the
    upstream stages unchanged. Keeps the result internally consistent (e.g. redoing the cast
    also redoes structure/chapters/prompts, which reference the cast; KDP is a leaf and redoes
    alone; worldbuilding also redoes the prompts, which embed its setting brief). The spec is
    taken from ``existing`` (see ``_REGEN_CASCADE``)."""
    if stage not in _REGEN_CASCADE:
        raise ValueError(f"unknown stage {stage!r}; expected one of {STAGE_LABELS}")
    spec = existing.spec
    models = stage_models or {}
    redo = set(_REGEN_CASCADE[stage])

    def be(s: str) -> Backend:
        return bind_model(backend, models.get(s))

    premise = existing.premise
    world = existing.worldbuilding
    characters = existing.characters
    outline = existing.outline
    breakdowns = existing.breakdowns
    writing_prompts = existing.writing_prompts
    kdp = existing.kdp

    if "premise" in redo:
        premise = premise_stage.generate(be("premise"), spec)
    if "worldbuilding" in redo:
        world = worldbuilding_stage.generate(be("worldbuilding"), spec, premise)
    if "characters" in redo:
        characters = characters_stage.generate(be("characters"), spec, premise, world)
    if "structure" in redo:
        _framework_name, beat_list = beats.select(spec.genre, spec.framework)
        outline = structure_stage.generate(be("structure"), spec, premise, characters, beat_list)
    if "chapters" in redo:
        breakdowns = chapters_stage.generate(be("chapters"), spec, premise, characters, outline)
    if "writing prompts" in redo:
        writing_prompts = prompts_stage.generate(spec, premise, world, characters, breakdowns)
    if "KDP metadata" in redo:
        kdp = metadata_stage.generate(be("KDP metadata"), spec, premise)

    return StoryBible(
        spec=spec,
        premise=premise,
        worldbuilding=world,
        characters=characters,
        outline=outline,
        breakdowns=breakdowns,
        writing_prompts=writing_prompts,
        kdp=kdp,
    )


def build(spec: Spec, backend: Backend, stage_models: dict[str, str] | None = None) -> StoryBible:
    result: StoryBible | None = None
    for event in build_iter(spec, backend, stage_models):
        if isinstance(event, StoryBible):
            result = event
    assert result is not None  # build_iter always yields a StoryBible last
    return result
