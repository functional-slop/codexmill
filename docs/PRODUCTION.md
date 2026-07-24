# Running CodexMill in production

CodexMill is built to be self-hosted. This is what's hardened, what you must set for a real deploy,
and what is not yet covered.

## Hardened (in the code)
- **Mandatory auth.** A fresh instance is *closed*: a local admin account (argon2id password) is
  created at first-run, and every route requires a session. Login is rate-limited; sessions are
  revocable (a per-identity epoch). Optional OIDC/SSO composes on top for shared instances. See
  `docs/SECURITY.md`.
- **Bounded inputs.** Every request field is capped (chapters 1-60, series books 1-12,
  chapters-per-book 1-40, string lengths), so a caller can't request an absurd amount of work or
  stuff the prompt. Out-of-bounds requests are rejected with `422` before any model call.
- **LLM-call timeout + clean errors.** Each model call times out (`CODEXMILL_TIMEOUT`, default 120s)
  with one retry; a hung/unreachable endpoint, bad key, unknown model, or rate limit becomes a
  plain-language error the user can act on — never a hang or a raw traceback.
- **SQLite WAL + busy timeout.** Concurrent generations don't hit "database is locked".
- **Security headers.** `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, and a
  strict `Content-Security-Policy` (the app loads no external resources; the CSP enforces that).
- **CSRF-safe session cookie.** `SameSite=lax` (blocks cross-site POSTs to `/api/generate` etc.).
- **Secrets at rest.** Stored API keys / OIDC secrets are **always** Fernet-encrypted — the key is
  `CODEXMILL_SECRET_KEY` or, if unset, an auto-generated `secret.key` persisted 0600 in the data dir.
  Encryption is never opt-in, and the API never echoes a stored key back.
- **Per-user quotas + a token-usage meter.** An opt-in cap of N generations per rolling window per
  user (off by default) is enforced on every generation endpoint; each generation reports its token
  tally. Turn the quota on in Settings for a shared instance.

## What you MUST set for a real deploy
1. **`CODEXMILL_SECRET_KEY`** — `openssl rand -hex 32`. Set it explicitly so encrypted secrets survive
   redeploys. (If unset the app auto-generates + persists one, but losing that `secret.key` makes
   stored keys undecryptable — so pin it for a real deploy.)
2. **Terminate TLS** (reverse proxy: Caddy/nginx/Traefik) and set **`CODEXMILL_HTTPS_ONLY=1`** so the
   session cookie is `Secure`.
3. **Guard first-run setup before exposing the instance.** Auth is mandatory, and the first person to
   reach a fresh instance creates the admin account. Before it's reachable from the internet, either
   create your admin account first, or set **`CODEXMILL_SETUP_TOKEN`** (a strong random value) so the
   setup page requires it — otherwise a stranger could claim the admin account first.
4. **Persist `CODEXMILL_CONFIG_DIR`** (the Docker image uses `/data`, a volume) — it holds the config
   store, the secret key, and `bibles.db`.
5. For a shared instance, turn on **OIDC login** (see README) so each user's library is isolated, and
   turn on the **per-user quota** in Settings so one user can't run up your engine cost.

## Not yet covered (know before you open it to the public)
- **Single-instance.** SQLite + a local config dir means one instance. Horizontal scaling would need
  a shared DB (Postgres) — not in this release.
- **No built-in metrics/alerting.** You get uvicorn's request logs; wire your own monitoring.

## Quick start (single trusted user, behind TLS)
```bash
export CODEXMILL_SECRET_KEY=$(openssl rand -hex 32)
export CODEXMILL_HTTPS_ONLY=1
export CODEXMILL_SETUP_TOKEN=$(openssl rand -hex 24)   # guards first-run setup on an exposed instance
docker compose up -d
# then reverse-proxy TLS to :8000, open Settings, configure your engine.
```
