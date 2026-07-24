# 10. Persistence: a saved bible library (SQLite)

Date: 2026-07-10 · Status: accepted · Roadmap milestone A

## Context
Generated bibles vanished after download. A polished app needs a library: save, list, reopen,
delete. This is the foundation the later milestones (regenerate-a-stage, exports, per-user data)
build on.

## Decision
`web/library.py`: a `Library` backed by **SQLite**, one file next to the config store
(`CODEXMILL_CONFIG_DIR/bibles.db`, default `~/.local/state/codexmill`). One `bibles` table stores
the full `StoryBible` JSON plus metadata (id, owner, created_at, title, genre). Every
`/api/generate` auto-saves; endpoints `GET /api/bibles`, `GET /api/bibles/{id}`,
`DELETE /api/bibles/{id}` manage the library. **Per-owner isolation**: owner = the session email
when OIDC is on, else `"local"` (so single-user/desktop just works). The front page shows a
"My bibles" list (open/delete).

## Consequences
- CodexMill's first real database. SQLite is right for single-instance and desktop; a Postgres
  backend can be added behind the same `Library` interface if multi-user hosting needs it.
- Bibles are stored as whole JSON documents — simple, and enough for reopen/export. Stage-level
  editing (milestone D) will read/patch that document.
- Owner isolation is enforced in every query, so turning on OIDC transparently scopes each user's
  library without further work.
