# 25. Multi-user accounts, admin controls, and OIDC

Date: 2026-07-14 · Status: accepted

## Context
ADR 0024 established a single local admin plus optional OIDC gated by an email allowlist. That
supports one operator, not a shared instance. Running an instance for several people requires real
accounts for both local and OIDC users, an admin surface to manage them, and per-user control over
who may consume the shared, server-configured model.

## Decision

### User model
A `users` table keyed on an immutable UUID `id`. Ownership references this id, never a mutable
username or email, so a rename or email change never orphans a user's data.

- `id` UUID PK, `username` unique, `email` (nullable), `password_hash` (nullable), `role` enum
  `{root, admin, user}`, `is_active` bool, `oidc_iss` + `oidc_sub` (nullable, unique together),
  `permissions` JSON, `created_at`, `updated_at`, `last_seen`.
- `password_hash` is nullable to allow the blank-password root recovery path (below). Hashing is
  argon2id (ADR 0024).
- Roles: `root` is the first account and cannot be edited or deleted by other admins; `admin` has
  full settings and user management; `user` generates and manages only their own bibles.
- Every authorization check is ANDed with `is_active`.

### Per-user permissions
`permissions` JSON, derived from role at creation:
- `use_server_engine` (bool): may use the server-configured LLM. If false, the user must supply
  their own base_url/key.
- `quota` (nullable): per-user generation cap (ADR 0022); null inherits the instance default.
- Admin capabilities (`manage_users`, `manage_settings`) are implied by `role`.

### Authentication methods
A settings record holds `active_auth_methods`, default `["local"]`:
- `["local"]`: local accounts only.
- `["local", "oidc"]`: both the login form and the SSO button are active.
- `["oidc"]`: SSO only; password login is disabled, with a local escape hatch at `/login?local=1`
  and the `auth_reset` CLI to re-enable local if locked out.

### OIDC
Authlib owns protocol correctness (ID-token signature, JWKS, discovery, `state`, `nonce`,
`aud`/`iss`/`exp`/`at_hash` validation, PKCE). The application layer must:
- Register with `server_metadata_url` and `client_kwargs={"scope": "openid email profile",
  "code_challenge_method": "S256"}` so PKCE is enabled for the confidential client.
- Use the high-level flow (`authorize_redirect` / `authorize_access_token`) with the same `request`
  object, backed by a signed session, so state/nonce/verifier are stored and validated.
- Persist `id_token` at login to support RP-initiated logout via `end_session_endpoint`.
- Key identity on the `(oidc_iss, oidc_sub)` pair, persisted on first link.
- Honor `email_verified`; never trust or auto-link an unverified email.
- Validate the post-login redirect target against an allowlist of relative paths.
- Ship an integration test that tampers with `aud`/`iss`/`nonce`/expiry and asserts each is rejected.

Admin-configurable OIDC settings:
- Provider: `issuer_url` with auto-discovery, per-endpoint overrides (authorization, token, userinfo,
  jwks, logout), `client_id`, `client_secret`, `signing_alg` (default RS256).
- UX: `button_text`, `auto_launch`, `login_custom_message`.
- Provisioning: `auto_register` (create a `user` on first successful login, else deny);
  `match_existing_by` = `unset | email | username` to link an SSO identity to a pre-existing account.
  The stored `(iss, sub)` is always matched first; `match_existing_by` is the fallback link, after
  which the sub is persisted. Email linking requires `email_verified` and refuses to relink an email
  already bound to a different sub.
- Authorization: `group_claim` mapped to a role (`admin`, then `user`; `root` never downgraded); an
  optional `advanced_perms_claim` mapped to the permission keys above; `admin_group` value. Group and
  claim restrictions are the preferred alternative to email-domain allowlists.
- Deployment: `redirect_subpath` for reverse-proxy subpath installs.

### Bootstrap and recovery
- First run creates the single `root` user with no default credentials (`needs_setup` flow).
- `python -m codexmill.auth_reset`: reset the root password, null the root hash for a one-time blank
  login, or force `active_auth_methods` to include `local`. This is the offline recovery path.

### Admin panel
Settings → Users: list all users with role, status, last seen, and usage; create a local user;
delete; enable/disable; reset another user's password; set role; set `use_server_engine` and per-user
`quota`. A non-root admin cannot edit or delete `root`.

## Consequences
- Larger authorization surface; a security audit follows implementation (auth bypass, per-user IDOR,
  OIDC linking/takeover, open redirect).
- The single-admin JSON `users` / `admin_emails` / OIDC config move to DB rows (ADR 0026); the
  existing admin becomes `root`; `admin_emails` seed `admin`-role users.
- `require_admin` and `current_owner` are expressed in terms of `role` + `permissions`. Owner-scoping
  of every bible read/write is unchanged but references `users.id`.
