# 26. Persistence foundation: SQLAlchemy + Alembic, SQLite default, Postgres option

Date: 2026-07-14 · Status: accepted

## Context
Persistence is currently split and hand-rolled: the bible library is hand-written SQLite SQL
(`web/library.py`, WAL + `BEGIN IMMEDIATE` for the quota decrement + ad-hoc `ALTER TABLE` migration
guards), while accounts and OIDC/LLM config live in a JSON file (`web/store.py`). ADR 0025 makes
users a relational entity that bibles must reference by foreign key, which the JSON store cannot
express. Foundational storage choices are cheapest to make before there is production data.

## Decision

### ORM and migrations
Adopt SQLAlchemy 2.0 (typed, `mapped_column`) for all persistence and Alembic for schema migrations,
replacing the bespoke `ALTER TABLE` guards. One baseline migration captures the current library
schema plus the new `users` and settings tables.

### One abstraction, two engines
`CODEXMILL_DATABASE_URL` selects the engine; unset resolves to
`sqlite:///<CONFIG_DIR>/codexmill.db`. Postgres is opt-in via `postgresql+psycopg://…`.
- SQLite remains the only requirement for self-hosting: zero-config, single file.
- Postgres is CI-tested so a large or multi-worker deployment is a config change, not a port.
- Engine specifics live in one place: SQLite gets `WAL`, `busy_timeout`, `foreign_keys=ON`; the
  quota's atomic decrement uses `BEGIN IMMEDIATE` on SQLite and `SELECT … FOR UPDATE` on Postgres,
  behind one repository method whose race-safety is proven by an integration test on both engines.

### Users and config move into the DB
Accounts (ADR 0025) and app/auth/OIDC/LLM settings become DB tables. `codexmill.json` is deprecated;
a one-time boot migration imports an existing file. Only bootstrap secrets stay outside the DB
(`CODEXMILL_SECRET_KEY` / the `secret.key` file, which encrypts secret columns at rest per ADR 0024).
Sealed values keep the `enc:v1:` Fernet scheme.

### Additional foundational choices
- Stable surrogate IDs: users get UUID PKs; ownership references `users.id`.
- UUID/ULID for user-facing resource IDs (bibles), so a future share-by-URL feature cannot leak
  counts or invite enumeration.
- All datetimes stored UTC and timezone-aware (`timestamptz` on Postgres, ISO-8601 UTC on SQLite).
- The login throttle and generation quota move into the DB so multiple workers share one source of
  truth, a prerequisite for running more than one worker.

### Request and session handling
Sync path operations continue to run in the threadpool (which is why concurrent SSE generations do
not block the event loop). A SQLAlchemy session is created and closed per request; the connection
pool bounds concurrency. Async DB drivers are out of scope for this change.

## Consequences
- `library.py` and `store.py` are refactored into SQLAlchemy models plus a thin repository layer,
  done while there is no production data to migrate.
- The quota's race-safety is re-proven on both engines via integration test.
- New deps: `sqlalchemy`, `alembic`, and `psycopg` (optional Postgres extra). The default Docker
  image stays SQLite-only.
- Implemented as one foundation pass with ADR 0025 and audited together.
