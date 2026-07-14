# 9. GUI-configurable OIDC + a persistent config store

Date: 2026-07-10 · Status: accepted · Amends ADR 0008

## Context
ADR 0008 shipped OIDC as env-only config. That's the infra/12-factor answer, but admins expect to
paste issuer/client-id/secret into a settings page, like nearly every app with OIDC. Doing that
needs three things CodexMill lacked: persistent state, a bootstrap that avoids locking yourself
out, and runtime reconfiguration (env is read once at boot).

## Decision
- **Config store** (`web/store.py`): a small JSON file, written atomically, `chmod 600`. Default
  path `~/.local/state/codexmill/codexmill.json` (outside the repo so a dev checkout stays clean);
  a deploy sets `CODEXMILL_CONFIG_DIR`. Holds OIDC settings, the admin email allowlist, and a
  persisted session secret (so sessions survive restarts). This is CodexMill's first stateful bit.
- **App factory takes a store.** OIDC config resolves **store-first, then env** (`resolve_oidc`),
  so the GUI wins but headless env deploys still work.
- **Runtime toggle, no restart.** The app resolves current OIDC per request and caches the Authlib
  client by config signature, rebuilding when settings change.
- **Admin surface**: `/admin` page + `GET/PUT /api/admin/oidc` and `POST /api/admin/oidc/test`
  (discovery probe). The API returns `has_secret`, never the secret value.
- **Bootstrap-safe authz**: the admin API is open while OIDC is unconfigured (set it up locally);
  once OIDC is on it requires an authenticated admin (email in the allowlist) OR the
  `CODEXMILL_SETUP_TOKEN` break-glass (header/query), so you can never permanently lock yourself out.

## Consequences
- Secret at rest lives in the config file, protected by file perms (acceptable for a single-tenant
  self-hosted tool; encrypt-at-rest is a later option if needed).
- Env OIDC (ADR 0008) remains fully supported and is reported as `source: env`.
- Verified live: configuring OIDC via the admin API flipped gating on with no restart and produced
  a correct redirect to the OIDC provider; the secret was persisted but not echoed back.
