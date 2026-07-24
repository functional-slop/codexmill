# 18. Series / continuity (series bible)

Date: 2026-07-10 · Status: accepted · Roadmap: competitive-parity #2

## Context
Our target audience (KDP rapid-release authors) writes SERIES, and Plot & Prompt itself sells
"series-potential premises". A single-book bible can't express a multi-book arc or keep the world
and cast consistent from book 1 to book 5 — which is exactly where a no-memory generator drifts
(the failure AIStoryWriter admits to). This is the bigger competitive-parity item; worldbuilding
(ADR 0015) was the small one.

## Decision
Add a **series** layer on top of the existing single-book pipeline, designed so continuity holds
**by construction** rather than by asking the model to "remember":

- **Shared world, carried cast.** The worldbuilding and the recurring character roster are generated
  **once** at the series level (seeded from the series arc) and **reused verbatim** in every book.
  A book never regenerates the world or the core cast, so they cannot drift.
- **Per-book generation with a "story so far".** Each book runs the book-level stages (premise →
  structure → chapters → writing prompts → KDP) seeded with: this book's plan, the series arc, the
  shared cast, and an accumulating recap of the prior books' loglines. Book N's premise is written
  to advance the arc from where book N-1 left off.
- **A plan stage up front.** `stages/series_plan.py` turns a `SeriesSpec` into a `SeriesPlan`: a
  series title, the overarching arc, and one `BookPlan` per book (title, arc role, premise hint).

### Data model (`schemas.py`)
- `SeriesSpec` (genre, tropes, series_premise_hint, `books`, `chapters_per_book`, pov, maturity,
  framework) — the user's request.
- `BookPlan` (number, title, arc_role, premise_hint); `SeriesPlan` (series_title, series_arc, books).
- `SeriesBible` (spec, plan, shared `worldbuilding`, `recurring_characters`, `books: list[StoryBible]`).
  Each per-book `StoryBible` **embeds** the shared world + cast so it still renders standalone.

### Pipeline (`series.py`)
`build_series_iter(spec, backend, stage_models)` yields `(stage_label, index, total)` progress like
`build_iter`, then a `SeriesBible`. It reuses the existing stage functions with the shared world/cast
injected (no new "book builder" duplication of stage logic). `build_series` is the sync wrapper.

### Rendering (`render.py`)
`render_series` shows the arc + book lineup + shared world + recurring cast **once**, then each book's
book-specific sections (premise/structure/breakdowns/prompts/KDP), reusing `bible_sections` and
filtering out the two shared sections per book.

## Consequences
- Continuity is structural: world + core cast are identical across books because they are the same
  objects, not re-asked. The remaining drift surface is per-book plots, which the "story so far"
  recap + shared cast constrain.
- v1 recap is deterministic (accumulated loglines), not an LLM "book summary" — cheaper and
  reproducible; a richer LLM recap stage is a possible later refinement.
- Books share one world, so this models a single continuous series, not an anthology with different
  settings. That matches the KDP rapid-release use case; a "same-world anthology" is out of scope.
- The fake backend gains a `SeriesPlan` special-case (like `Outline`) so offline tests honor the
  requested book count.
- Naming consistency: the recurring cast is generated from the user's series seed FIRST, then passed
  into the plan stage, so the plan's book lineup uses the real character names (an early draft
  generated the plan before the cast and the lineup could name a protagonist the cast then
  contradicted — fixed by the cast-before-plan order). The base premise that seeds world+cast comes
  from `series_premise_hint` rather than a plan-invented arc.
- Follow-ups after the engine: `/api/series` CRUD + SSE (done), a "Series" UI mode (done),
  series-level `.docx`/Obsidian export (done), and regenerate-a-book (done).
