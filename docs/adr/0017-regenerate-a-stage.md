# 17. Regenerate a single stage

Date: 2026-07-10 · Status: accepted · Roadmap: D (output polish + regenerate)

## Context
Generation was all-or-nothing: dislike the cast, or one chapter breakdown, and you re-ran the whole
bible (7 LLM calls, and everything else changed too). Roadmap D calls for redoing one stage.

## Decision
`pipeline.regenerate(backend, existing, stage, stage_models=None)` re-runs the target stage of a
stored `StoryBible` **and every stage that consumes its output**, reusing the upstream stages
unchanged, so the result stays internally consistent. The cascade (`_REGEN_CASCADE`) is derived
from the stage signatures in `build_iter`:

- `worldbuilding`, `KDP metadata` — leaves, redo alone.
- `characters` → also `structure`, `chapters`, `writing prompts` (they reference the cast).
- `structure` → also `chapters`, `writing prompts`.
- `chapters` → also `writing prompts` (deterministic re-assembly, no LLM call).
- `premise` → redoes everything (it feeds every stage).

Served by `POST /api/bibles/{id}/regenerate` `{stage, [engine overrides]}`, owner-scoped like the
other bible routes. It patches the stored row **in place** via `Library.update` (same id + created_at)
rather than creating a new bible. The web toolbar gains a stage picker + "Regenerate" button, shown
only when a saved bible is open and an engine is configured; each option names what it cascades to.

Engine overrides reuse the same precedence as generate (request > saved > env); `GenerateRequest`
and `RegenerateRequest` now share a `_LLMOverrides` base so `effective_settings` serves both.

## Consequences
- Regenerating an early stage still costs several LLM calls (the cascade), but never silently
  desyncs the bible, which a naive single-stage overwrite would.
- The spec is taken from the stored bible; regeneration does not currently let you change the spec
  (genre/chapters). Editing the spec + regenerating is a possible later refinement.
- `Library` gained an `update` (in-place overwrite); the id and creation time are preserved so the
  library list order and any external references stay stable.
