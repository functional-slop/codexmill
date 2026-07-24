# 21. Per-generation usage meter (Milestone F.1)

Date: 2026-07-11 · Status: accepted · Roadmap: F

## Context
A story bible is 6+ model calls, and the chapters stage adds one call per chapter — a single
generation on a paid endpoint can quietly spend a non-trivial amount of tokens. Friends running the
beta point CodexMill at their own OpenAI/Gemini/OpenRouter key; nothing today tells them how much a
run cost, so the first signal is the provider's bill. Milestone F is abuse & cost controls; F.1 is
the meter: surface, per generation, how many tokens it used.

## Decision
- **Measure at the backend, not the stages.** Every stage talks to a `Backend`; only `OpenAIBackend`
  knows the real token counts (the OpenAI SDK returns `resp.usage`). So the meter is a `Usage`
  accumulator (`prompt_tokens`, `completion_tokens`, `total_tokens`, `calls`) that lives **on the
  backend instance** and is incremented on every successful `chat.completions.create` — including the
  JSON-repair retries, because each retry is a real, billed round-trip. No stage or pipeline signature
  changes: the pipeline already threads one backend through every stage, and `_BoundBackend`
  (per-stage model override) shares the inner backend's meter, so the tally covers the whole run.
- **Tokens, not dollars.** Tokens are what providers bill on and are reported authoritatively by the
  API; a dollar figure needs a per-model price table that goes stale and is meaningless for free local
  Ollama. We show the token counts and let the user apply their own rate. (A future, user-overridable
  "price per 1M tokens" → estimated cost is a possible F follow-up, deliberately out of scope here.)
- **Surface it everywhere a generation happens.** `GenerateResponse` gains a `usage: Usage` field
  (default empty, so read-only endpoints like loading a saved bible report zero — correct, loading
  costs nothing). The SSE `done` events for both single-book and series streams carry `usage`. The web
  UI shows it as a small "· 12,480 tokens" note next to the word count after a run (and after a
  regenerate). It is per-run/ephemeral and not persisted with the bible — the meter answers "what did
  *this* generation cost", not a lifetime total.
- **Fake backend synthesizes a placeholder.** The offline `fake` backend has no real tokens, so it
  estimates deterministically from the prompt length (`len(system+user)//4` prompt, a fixed
  completion) and counts each call. This keeps the plumbing exercised and testable offline with zero
  cost, and is clearly synthetic (never presented as a real bill).

## Consequences
- Non-intrusive: the accumulator is additive; `usage` defaults to empty so nothing that doesn't
  generate has to change. Endpoints that generate read `backend.usage` after the run.
- If an endpoint omits `usage` in its response (some OpenAI-compatible servers do), that call adds
  zero tokens but still counts as a call — the meter under-reports tokens rather than lying.
- Sets up F.2 (per-user quotas): the same per-run tally is the unit a quota would sum over a window.
