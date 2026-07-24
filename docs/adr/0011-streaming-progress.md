# 11. Streaming per-stage progress

Date: 2026-07-10 · Status: accepted · Roadmap milestone B

## Context
Generation is 7+ model calls and takes 30-120s. A single spinner with no feedback reads as
"broken" to a normal user — the #1 UX complaint waiting to happen.

## Decision
- `pipeline.build_iter(spec, backend)` yields a `(stage_label, index, total)` tuple as each stage
  starts, then the final `StoryBible`. `pipeline.build` is now a thin consumer of it, so the CLI,
  tests, and the non-streaming `/api/generate` are unchanged.
- New `POST /api/generate/stream` returns a Starlette `StreamingResponse` (`text/event-stream`)
  that emits SSE `data:` frames: `{stage,index,total}` per stage, then `{done,id,filename,markdown}`
  (which also saves to the library), or `{error}`. The generator is synchronous and blocks on the
  LLM calls; Starlette runs a sync StreamingResponse body in a threadpool, so the event loop stays
  free.
- The front page reads the stream with `fetch` + a `ReadableStream` reader (not `EventSource`,
  which is GET-only and can't carry the POST body), showing "Generating… chapters (4/6)".

## Consequences
- Stage-level granularity for now ("chapters"); finer per-chapter progress can come later by
  having the chapters stage yield sub-events.
- Two generate endpoints (stream + plain). The plain one stays the simple programmatic/CLI path
  and keeps the smoke test trivial; the stream one is the UI path.
