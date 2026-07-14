"""Multi-book series generation (ADR 0018). Worldbuilding + the recurring cast are generated once
and shared by every book, so they can't drift; each book advances the arc seeded with a "story so
far" recap of the prior books. Reuses the single-book stage functions (see docs/adr/0004)."""

from __future__ import annotations

from collections.abc import Iterator

from codexmill import beats
from codexmill.llm import Backend, bind_model
from codexmill.schemas import (
    BookPlan,
    CharacterSet,
    Premise,
    SeriesBible,
    SeriesPlan,
    SeriesSpec,
    Spec,
    StoryBible,
    Worldbuilding,
)
from codexmill.stages import chapters as chapters_stage
from codexmill.stages import characters as characters_stage
from codexmill.stages import metadata as metadata_stage
from codexmill.stages import premise as premise_stage
from codexmill.stages import prompts as prompts_stage
from codexmill.stages import series_plan as series_plan_stage
from codexmill.stages import structure as structure_stage
from codexmill.stages import worldbuilding as worldbuilding_stage

# Per-book stage labels (streamed as "book 2 · structure"). Series-level labels are fixed below.
_BOOK_STAGES = ["premise", "structure", "chapters", "writing prompts", "KDP metadata"]


def _book_spec(spec: SeriesSpec, plan: SeriesPlan, book: BookPlan, recap: str, cast: str) -> Spec:
    story_so_far = recap or "this is the first book in the series"
    hint = (
        f"{book.premise_hint}\n\n"
        f"This is book {book.number} of {len(plan.books)} in the series "
        f'"{plan.series_title}" (arc role: {book.arc_role}).\n'
        f"Series arc: {plan.series_arc}\n"
        f"Story so far: {story_so_far}\n"
        f"Recurring cast to feature: {cast}."
    )
    return Spec(
        genre=spec.genre,
        tropes=spec.tropes,
        premise_hint=hint,
        chapters=spec.chapters_per_book,
        pov=spec.pov,
        maturity=spec.maturity,
        framework=spec.framework,
    )


def build_series_iter(
    spec: SeriesSpec, backend: Backend, stage_models: dict[str, str] | None = None
) -> Iterator[tuple[str, int, int] | SeriesBible]:
    """Run the series pipeline, yielding ``(stage_label, index, total)`` as each stage starts and
    finally the assembled ``SeriesBible``. Same streaming contract as ``pipeline.build_iter``."""
    models = stage_models or {}
    total = 3 + len(_BOOK_STAGES) * spec.books  # plan + world + cast, then 5 stages per book
    step = 0

    def be(stage: str) -> Backend:
        return bind_model(backend, models.get(stage))

    def tick(label: str) -> tuple[str, int, int]:
        nonlocal step
        step += 1
        return (label, step, total)

    # A base spec/premise seeded from the user's series idea drives the shared world + recurring
    # cast (once). Cast is generated BEFORE the plan so the plan's book lineup names the real cast.
    base_spec = Spec(
        genre=spec.genre,
        tropes=spec.tropes,
        premise_hint=spec.series_premise_hint,
        chapters=spec.chapters_per_book,
        pov=spec.pov,
        maturity=spec.maturity,
        framework=spec.framework,
    )
    base_premise: Premise = premise_stage.generate(be("premise"), base_spec)
    yield tick("worldbuilding")
    world: Worldbuilding = worldbuilding_stage.generate(
        be("worldbuilding"), base_spec, base_premise
    )
    yield tick("recurring cast")
    cast: CharacterSet = characters_stage.generate(be("characters"), base_spec, base_premise)
    cast_names = ", ".join(c.name for c in cast.characters)
    yield tick("series plan")
    plan = series_plan_stage.generate(be("series plan"), spec, cast)
    # Correct the streamed total now that we know how many books the plan actually produced
    # (a real backend may not return exactly spec.books), so index/total stays accurate.
    total = 3 + len(_BOOK_STAGES) * len(plan.books)

    _framework_name, beat_list = beats.select(spec.genre, spec.framework)
    books: list[StoryBible] = []
    recap = ""
    for book in plan.books:
        book_spec = _book_spec(spec, plan, book, recap, cast_names)
        yield tick(f"book {book.number} · premise")
        bpremise = premise_stage.generate(be("premise"), book_spec)
        yield tick(f"book {book.number} · structure")
        outline = structure_stage.generate(be("structure"), book_spec, bpremise, cast, beat_list)
        yield tick(f"book {book.number} · chapters")
        breakdowns = chapters_stage.generate(be("chapters"), book_spec, bpremise, cast, outline)
        yield tick(f"book {book.number} · writing prompts")
        writing_prompts = prompts_stage.generate(book_spec, bpremise, world, cast, breakdowns)
        yield tick(f"book {book.number} · KDP metadata")
        kdp = metadata_stage.generate(be("KDP metadata"), book_spec, bpremise)
        books.append(
            StoryBible(
                spec=book_spec,
                premise=bpremise,
                worldbuilding=world,
                characters=cast,
                outline=outline,
                breakdowns=breakdowns,
                writing_prompts=writing_prompts,
                kdp=kdp,
            )
        )
        recap += f" Book {book.number} ({book.title}): {bpremise.logline}"

    yield SeriesBible(
        spec=spec,
        plan=plan,
        worldbuilding=world,
        recurring_characters=cast,
        books=books,
    )


def regenerate_book(
    backend: Backend,
    series: SeriesBible,
    book_number: int,
    stage_models: dict[str, str] | None = None,
) -> SeriesBible:
    """Re-run a single book of an existing series, reusing the shared world + cast and the recap of
    the books BEFORE it, and return a new SeriesBible with that book replaced. Books AFTER it are
    left as-is (regenerating a middle book does not re-thread later books)."""
    if not 1 <= book_number <= len(series.books):
        raise ValueError(f"book must be 1..{len(series.books)}, got {book_number}")
    models = stage_models or {}
    plan = series.plan
    world = series.worldbuilding
    cast = series.recurring_characters
    cast_names = ", ".join(c.name for c in cast.characters)

    def be(stage: str) -> Backend:
        return bind_model(backend, models.get(stage))

    recap = ""
    for bp, bk in zip(plan.books, series.books, strict=False):
        if bp.number >= book_number:
            break
        recap += f" Book {bp.number} ({bp.title}): {bk.premise.logline}"

    target = plan.books[book_number - 1]
    book_spec = _book_spec(series.spec, plan, target, recap, cast_names)
    bpremise = premise_stage.generate(be("premise"), book_spec)
    _framework_name, beat_list = beats.select(series.spec.genre, series.spec.framework)
    outline = structure_stage.generate(be("structure"), book_spec, bpremise, cast, beat_list)
    breakdowns = chapters_stage.generate(be("chapters"), book_spec, bpremise, cast, outline)
    writing_prompts = prompts_stage.generate(book_spec, bpremise, world, cast, breakdowns)
    kdp = metadata_stage.generate(be("KDP metadata"), book_spec, bpremise)
    new_book = StoryBible(
        spec=book_spec,
        premise=bpremise,
        worldbuilding=world,
        characters=cast,
        outline=outline,
        breakdowns=breakdowns,
        writing_prompts=writing_prompts,
        kdp=kdp,
    )
    books = list(series.books)
    books[book_number - 1] = new_book
    return series.model_copy(update={"books": books})


def build_series(
    spec: SeriesSpec, backend: Backend, stage_models: dict[str, str] | None = None
) -> SeriesBible:
    result: SeriesBible | None = None
    for event in build_series_iter(spec, backend, stage_models):
        if isinstance(event, SeriesBible):
            result = event
    assert result is not None  # build_series_iter always yields a SeriesBible last
    return result
