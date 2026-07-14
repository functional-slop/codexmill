# Security posture

CodexMill is a **mass-market, local-first self-hosted app**. It must be
secure on its own, with no external identity provider — the local account IS the auth.

## Model
- **Auth is mandatory (ADR 0024).** A fresh instance is *closed*: only `/api/me` (to drive the
  first-run UI) and `/api/auth/setup` respond until a local admin account exists. After that every
  route requires a session. Setup refuses once an account exists, and is not available when OIDC is
  configured — it can't be used to take over an instance.
- **Passwords:** argon2id (`argon2-cffi`, OWASP first choice), per-password salt, constant-time
  verify, rehash-on-login, and a dummy-verify on unknown users so response timing can't enumerate
  usernames. Login is rate-limited (5 failures / 5 min per client address → 429).
- **Sessions:** Starlette signed cookies (HMAC via the persisted 256-bit `session_secret`),
  `HttpOnly`, `SameSite=lax`, capped lifetime (7d, `CODEXMILL_SESSION_MAX_AGE`). A per-identity
  **session epoch** is stamped into each cookie and checked per request; logout / password change
  rotates it, so a captured cookie stops working after logout (stateless-cookie revocation).
- **Secrets at rest:** always encrypted (Fernet). The key is `CODEXMILL_SECRET_KEY` or an
  auto-generated, persisted `secret.key` — encryption is never opt-in. The config store and key file
  are written 0600-from-creation (atomic `O_EXCL`), never briefly world-readable.
- **OIDC/SSO (optional)** composes on top for shared instances; admin = the local account, an OIDC
  user on the email allowlist (fail-closed on an empty allowlist), or the `CODEXMILL_SETUP_TOKEN`
  break-glass (header-only, never in a URL).

## Audited (2026-07-12) — 4 independent adversarial reviews
Auth mechanism, secret leakage, endpoint authorization / injection / SSRF / traversal, and
packaging/supply-chain. **Confirmed clean:** no IDOR (every read/list/export/regenerate/delete
is owner-scoped), no SQL injection (all queries parameterized), no XSS (the markdown renderer escapes
before insert and emits no attributes/links), no path traversal or zip-slip (`slugify` restricts to
`[a-z0-9-]`), no eval/exec/pickle/unsafe-yaml, no secret in git history or bundled in the kit/binary,
Docker runs non-root on a frozen lockfile. Findings were fixed in commit `cabb495` (see CHANGELOG).

## Accepted-by-design tradeoffs (NOT defects — do not "fix" into something worse)
- **SSRF via a user-supplied `base_url`.** Generation connects to whatever OpenAI-compatible endpoint
  the user provides. Internal/loopback addresses **cannot** be blocked because pointing at your local
  Ollama at `http://127.0.0.1:11434/v1` (or a LAN IP) is the primary use case. For a single-user
  local instance this is inert. On a multi-user OIDC instance, any authed user can make the server
  fetch an internal URL — enable network egress controls at the proxy if that matters.
- **Rate-limit / generation quota is OFF by default** (ADR 0022). Correct for single-user (you don't
  throttle yourself); a shared instance should turn it on in Settings.
- **`X-Forwarded-For` is not trusted** (login throttle keys on the socket peer). Correct default —
  trusting it would let an attacker forge a fresh key per request. Behind a reverse proxy the throttle
  degrades to a single bucket (a whole-instance lockout is possible); acceptable for self-host scale.
- **Plain HTTP:** the session cookie isn't `Secure` unless `CODEXMILL_HTTPS_ONLY=1` (most local
  deploys have no TLS). Put it behind a TLS proxy for a network-exposed instance.

## Deploy notes
- **Preserve `CODEXMILL_SECRET_KEY` / `secret.key`** across updates or sealed values (the stored API
  key) become undecryptable.
- If you deploy by pip-installing deps at container start (rather than the pinned `uv.lock`), pin them
  to **exact versions**, so a redeploy can't pull a newly-published malicious release. The bundled
  `Dockerfile` already installs from the frozen `uv.lock`, which is the safe default.
