# 8. Optional OIDC login for the web UI

Date: 2026-07-10 · Status: accepted

## Context
A publicly hosted instance needs a way to authenticate users (abuse control, rate limiting,
associating usage). But CodexMill is also meant to be self-hosted freely by anyone (AGPL), and
forcing every self-hoster to stand up an identity provider would defeat that.

## Decision
Add OIDC login that is **optional and off by default**, engaged only when fully configured via
env (`CODEXMILL_OIDC_ISSUER`, `_CLIENT_ID`, `_CLIENT_SECRET`, `_SESSION_SECRET`). Any OIDC
provider works via its discovery URL (Authentik, Google, Auth0, ...). Implemented with Authlib +
Starlette session cookies (`codexmill.web.auth`), wired through an app factory `create_app(oidc)`.

Posture, when enabled:
- **Gate the expensive action, not the page.** `/api/generate` requires a logged-in session
  (401 otherwise). The page still loads; `/api/me` reports `{oidc_enabled, authenticated}` and
  the front end shows a Sign-in link. Right default for a bring-your-own-key tool.
- Routes: `/auth/login`, `/auth/callback`, `/auth/logout`. Discovery is lazy (no network at
  import/registration).

## Consequences
- New runtime deps: `authlib`, `httpx`, `itsdangerous`.
- Local self-host is unchanged (no env → open), so the give-it-away path is preserved.
- Offline tests cover the gating (open when off; 401 + `/api/me` when on). The live login
  round-trip needs real IdP client credentials and is verified separately by the operator.
- Behind a reverse proxy, the callback URL is derived from the request; a proxy that rewrites
  scheme/host may need `X-Forwarded-*` handling (follow-up if/when hosted).
- SESSION_SECRET must be stable across restarts (env-provided), or sessions invalidate on deploy.
