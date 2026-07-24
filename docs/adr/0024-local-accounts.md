# 24. Local admin account + always-on secret encryption

Date: 2026-07-12 · Status: accepted
> **Supersedes the auth + secrets-at-rest posture of ADR 0008, 0009, and 0012** (optional/off login,
> open-until-OIDC admin, opt-in encryption). Multi-user roles + OIDC provisioning extend this in ADR 0025.

## Context
Two defaults were wrong:

1. **The app shipped with no login.** Access control was "OIDC or nothing", and OIDC is off by
   default, so any un-configured instance had an **open admin surface** (anyone who could reach it
   could set/overwrite the API key, disable auth, read the library). "It's LAN-only" is not an answer:
   every comparable self-hosted app now ships **mandatory username+password auth**, created at first
   run. We must match that bar.
2. **Secrets were plaintext by default.** `crypto.seal()` was a no-op unless the operator remembered
   to set `CODEXMILL_SECRET_KEY`, so the stored LLM API key and OIDC client secret sat in
   `codexmill.json` as cleartext on every default deploy. Encryption must not be opt-in.

## Decision
- **A local admin account is required.** First run forces creating a username + password (in the
  onboarding flow, before anything else). Once an account exists, **every** API route and page
  requires an authenticated session. There is no "open" mode after setup.
  - Bootstrap is narrow and self-closing: while **no** account exists and OIDC is off, only
    `GET /api/me` (to learn `needs_setup`) and `POST /api/auth/setup` are reachable; everything else
    401s. `POST /api/auth/setup` refuses once any account exists, so it cannot be used to add a
    second admin or re-take the instance.
- **Password hashing: argon2id** (`argon2-cffi`), the OWASP first-choice KDF, with the library's
  current defaults and a per-password salt. Verification is constant-time and **rehashes on login**
  when parameters change (`needs_rehash`). Not a homegrown hash, not a bare digest.
- **Auth methods compose.** A session is valid if it came from the local account **or** from OIDC.
  OIDC stays optional and unchanged (SSO for a shared instance); the local account is the always-there
  floor. `require_admin` accepts: the local admin, an OIDC user on the admin allowlist, or the
  break-glass `CODEXMILL_SETUP_TOKEN`.
- **Secrets are ALWAYS encrypted.** `crypto` now resolves a key as: `CODEXMILL_SECRET_KEY` env →
  otherwise a key file (`secret.key`, chmod 600) in `CODEXMILL_CONFIG_DIR`, **auto-generated on first
  use**. So a default deploy encrypts the API key/OIDC secret at rest with no operator action.
  Plaintext is no longer reachable by forgetting an env var. (Losing the key file = the sealed values
  can't be decrypted; that is the correct failure and is documented.)
- **Login lives in the app**, not a separate page: a `login` view in the SPA (`POST /api/auth/login`,
  `POST /api/auth/logout`), and a "Create your admin account" step as **step 1 of onboarding**.

## Consequences
- Breaking for existing instances: an instance with no account now shows the create-admin step on
  next load. That is intended — it closes the open-admin hole.
- `argon2-cffi` is a new runtime dependency (wheels on every platform we ship: Docker and a
  pip-install-on-start container).
- Setting `CODEXMILL_SECRET_KEY` is now belt-and-braces rather than load-bearing; the app generates
  and persists its own key if you don't provide one.
- Rate limits/quotas become meaningful per real user, and `current_owner` is now a real identity for
  local accounts (the username) rather than the shared `local`.
- **No library migration for pre-auth instances.** Rows saved before auth existed
  are owned by the legacy `local` string and simply become unreachable after an account is created.
  This is deliberate: a story bible is an artifact the user **exports** (`.md`/`.docx`/Obsidian), and
  the library is a convenience, not a system of record. Carrying migration code for a one-time
  transitional case isn't worth the complexity — if you didn't export it, that's the same as closing
  Word without saving.
