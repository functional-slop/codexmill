# Running CodexMill in production

CodexMill is built to be self-hosted. This is what's hardened, what you must set for a real deploy,
and what is not yet covered.

## Hardened (in the code)
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
- **Secrets at rest.** Stored API keys / OIDC secrets are Fernet-encrypted when `CODEXMILL_SECRET_KEY`
  is set; the API never echoes a stored key back.

## What you MUST set for a real deploy
1. **`CODEXMILL_SECRET_KEY`** — `openssl rand -hex 32`. Encrypts stored secrets at rest. Without it,
   keys sit in a chmod-600 JSON file in plaintext.
2. **Terminate TLS** (reverse proxy: Caddy/nginx/Traefik) and set **`CODEXMILL_HTTPS_ONLY=1`** so the
   session cookie is `Secure`.
3. **Lock down `/admin` before exposing the instance.** `/admin` is open until OIDC is configured
   (the first-run setup pattern). Either configure OIDC immediately, or set **`CODEXMILL_SETUP_TOKEN`**
   (a strong random value) so admin requires it — do this *before* the instance is reachable from the
   internet, so a stranger can't reach the setup page first.
4. **Persist `CODEXMILL_CONFIG_DIR`** (the Docker image uses `/data`, a volume) — it holds the config
   store and `bibles.db`.
5. For a shared instance, turn on **OIDC login** (see README) so generations require a signed-in user
   and each user's library is isolated.

## Not yet covered (know before you open it to the public)
- **No rate limiting / quotas.** A single authenticated user can still issue many generations and run
  up cost/load on your engine. This is Milestone F (per-user quotas + a per-generation cost meter).
  Until then, only expose CodexMill to trusted users, keep it behind OIDC, and/or put a rate limit in
  your reverse proxy.
- **Single-instance.** SQLite + a local config dir means one instance. Horizontal scaling would need
  a shared DB (Postgres) — not built.
- **No built-in metrics/alerting.** You get uvicorn's request logs; wire your own monitoring.

## Quick start (single trusted user, behind TLS)
```bash
export CODEXMILL_SECRET_KEY=$(openssl rand -hex 32)
export CODEXMILL_HTTPS_ONLY=1
export CODEXMILL_SETUP_TOKEN=$(openssl rand -hex 24)   # until you configure OIDC
docker compose up -d
# then reverse-proxy TLS to :8000, open Settings, configure your engine.
```
