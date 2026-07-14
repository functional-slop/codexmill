# 14. Drop the fake "offline demo" engine; bundle a real sample

Date: 2026-07-10 · Status: accepted · Amends ADR 0013

## Context
The UI offered "Offline demo" as an engine. But an offline demo can't *generate* anything — there
is no AI to write it. It only produced output because `FakeBackend` returns hardcoded placeholder
text (a bakery romance) and ignores the user's input entirely. So a user asking for "hard sci-fi"
got a cozy bakery story under a "Genre: hard sci-fi" header — misleading, and it pretended to
generate when nothing was generated.

## Decision
- **Remove "Offline demo" as a user-facing engine.** `FakeBackend` stays, but only as the
  deterministic **test** fixture (the `/api/generate*` endpoints still accept `backend:"fake"` for
  the suite); it is no longer offered in the provider dropdown or used as a main-page fallback.
- **Bundle a real pre-generated sample** at `web/static/sample.md` (a 6-chapter epic-fantasy bible,
  ~11k words, generated with Gemini). The main page has a **"See a sample"** link that loads it —
  honest about being a fixed example, not a fake generation.
- **No engine configured → nudge, don't fake.** When no engine is set, the main page shows
  "No AI engine set up yet" and the button becomes "Set up an AI engine →" (to Settings), plus the
  sample link. Generation only happens with a real configured engine.

## Consequences
- Honest UX: the zero-setup path is "view a real sample," not "generate fake content."
- The sample ships in the wheel (it's under `web/static`) and is viewable/downloadable.
- Tests are unaffected — they still drive the pipeline with `FakeBackend` via `backend:"fake"`.
- Refresh the sample occasionally so it reflects current output quality.
