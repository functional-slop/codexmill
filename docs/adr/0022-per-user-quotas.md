# 22. Per-user generation quotas (Milestone F.2)

Date: 2026-07-11 · Status: accepted · Roadmap: F

## Context
F.1 shows a run's token cost; F.2 is the enforcement half. An operator who exposes CodexMill to
other people (OIDC on, or an open shared instance) needs a ceiling so one user can't run the
operator's paid key dry. The single-container beta model (a friend runs their own instance against
their own key) needs *no* limit — so this must be strictly opt-in and invisible until configured.

## Decision
- **Opt-in, off by default.** A quota of `max_generations` per `window_hours`, stored in the config
  store under `rate_limit` (admin-editable) with an env fallback (`CODEXMILL_MAX_GENERATIONS`,
  `CODEXMILL_RATE_WINDOW_HOURS`) for headless deploys. `max_generations = 0` means unlimited and is
  the default — so beta friends and any un-configured instance are unaffected and never hit a limit.
- **Unit = one LLM-invoking request, per owner, over a rolling window.** Every endpoint that calls
  the model to build or regenerate — `/api/generate`, `/api/generate/stream`, `/api/series`,
  `/api/series/stream`, and the two regenerate endpoints — consumes one slot. Owner is the same
  identity the library uses (session email when OIDC is on, else `local`), so on a real multi-user
  instance each person gets their own budget; on a single-user instance all traffic is one owner.
- **Count the attempt, not just success.** The slot is consumed when a generation *starts* (after
  input validation, before the model calls), because a started run already spends tokens. This favors
  cost/abuse protection over letting failed-but-billed runs be free. (Input-shape 422s reject earlier,
  before any slot is taken.)
- **Storage: a `rate_events(owner, ts)` table in the library SQLite DB.** One method,
  `Library.try_consume(owner, limit, window_hours) -> (allowed, used)`: count the owner's events since
  `now - window`; if `>= limit`, deny; else insert an event, prune that owner's expired rows, allow.
  Same DB as bibles (WAL, busy_timeout) — no new store. Good enough concurrency for self-host scale
  (no cross-request lock; a rare race can let a couple extra through, never fewer — acceptable).
- **Deny = HTTP 429** with a plain-language `detail` (the limit + window) and a `Retry-After` header.
  For the SSE endpoints the check runs *before* the stream opens, so it's an ordinary JSON 429 the
  existing frontend error path already surfaces ("Generation failed: …"), not an in-stream error.
- **Admin surface:** GET/PUT `/api/admin/rate-limit` and a small form in the `/admin` **Access &
  Login** tab (it's an access-control concern), showing current limit/window and the effective source.

## Consequences
- Zero behavior change for the default (unlimited) case: `enforce_quota` returns immediately when the
  effective limit is 0, so no DB write happens on un-configured instances.
- The `rate_events` table is self-pruning per owner on each consume, so it stays small.
- Builds directly on F.1's per-run framing; a future refinement could budget by *tokens* per window
  (using F.1's tally) instead of by request count — deferred; request-count is simpler and legible.
