# 7. Web front end: FastAPI + one self-contained page

Date: 2026-07-10 · Status: accepted

## Context
The audience is indie authors, not developers. A CLI reaches almost none of them, and the paid
products win entirely on "type a premise, click, download." Without that experience CodexMill
cannot disrupt anything; it's a good engine with no reach.

## Decision
Ship a web UI as a core deliverable: a **FastAPI** backend (`codexmill.web.app`) serving **one
self-contained static HTML page** (no npm, no build step, inline CSS/JS). Launched via
`codexmill serve`. The page is a form (genre, tropes, premise, chapters, POV, heat, words) plus
an engine choice: offline demo, local Ollama, or free cloud with a bring-your-own-key field. On
submit it POSTs to `/api/generate`, which runs the shared `pipeline.build` and returns the
rendered Markdown for preview + download.

Key points:
- The **browser talks only to our server; our server talks to the LLM.** No browser CORS to a
  local Ollama, and the writer installs nothing but this one app (or uses a hosted instance).
- **Bring-your-own-key.** Keys from the form are used for that request only and never stored.
  This is what makes a hosted instance free to run and keeps the AGPL "self-host it yourself"
  path real.
- **Shared core.** Web and CLI both call `pipeline.build` / `render_bible`; no logic forks.
- **Same test discipline.** The API is tested by calling it (`fastapi.testclient`) with the
  offline fake backend — deterministic, no network. See `tests/test_web.py`.

## Consequences
- New runtime deps: `fastapi`, `uvicorn` (core); `httpx` (dev, for TestClient).
- No build toolchain, so the front end stays trivially self-hostable — the point, for saturation.
- A React/SPA rewrite is possible later if richer UX (per-stage progress streaming) is wanted;
  the plain page is the deliberate v1 to keep the barrier to running it near zero.
- Packaging note (follow-up): ensure `web/static/*` ships in the built wheel; editable/source
  installs already resolve it via `__file__`.
