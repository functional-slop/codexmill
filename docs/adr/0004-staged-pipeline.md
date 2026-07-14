# 4. Staged pipeline with validated hand-offs

Date: 2026-07-10 · Status: accepted

## Context
A coherent 30k–45k word story bible cannot be produced by one prompt. The paid products build
it as a chain of small, templated steps. We do the same, but in the open.

## Decision
The generator is a linear pipeline of small stages (`src/codexmill/stages/`), one file each:
premise → characters → structure → (roadmap: chapters → prompts → metadata). Each stage takes
the accumulating context, calls the backend for a *single* focused output, and returns a
validated pydantic model (`schemas.py`). Later stages that write long, drift-prone content
(chapters) thread a rolling summary so context per call stays small and consistent.

## Consequences
- No stage needs a large context window or a large model; the tool degrades gracefully onto
  weak hardware.
- Stages are independently testable and swappable.
- Consistency ("voice", no drift) is achieved by injecting voice sheets + rolling summaries into
  each prompt — a data-plumbing property, not model magic.
- One stage per commit keeps history legible.
