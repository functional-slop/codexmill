# 12. Saved server-side LLM settings + secrets-at-rest

Date: 2026-07-10 · Status: accepted · Roadmap milestone C (part 1)

## Context
The engine/model/key was per-request only — users re-pasted a key every generation, and once we
persist keys server-side we must not store them in plaintext.

## Decision
- **Saved LLM defaults** in the config store (`store.get_llm/set_llm`): backend, base_url, model,
  api_key. Admin manages them at `/admin` (a new "LLM defaults" section) via
  `GET/PUT /api/admin/llm` and a `POST /api/admin/llm/test` that lists the endpoint's `/models`.
  `GET` returns `has_key`, never the key.
- **Resolution precedence** for a generation: per-request override > saved server default > env
  (`web/app.py:effective_settings`). Both `/api/generate` and `/api/generate/stream` use it.
- **Front page**: `/api/me` exposes `has_llm_default`; when set, the form offers a "Server default"
  engine (selected by default) that sends no creds, so users generate with one click.
- **Secrets at rest** (`web/crypto.py`): if `CODEXMILL_SECRET_KEY` is set, the stored LLM api_key
  and OIDC client_secret are Fernet-encrypted (key = SHA-256 of the env secret), marked with an
  `enc:v1:` prefix. Without the key they're stored as-is (file perms only) — same posture as before,
  now opt-in upgradeable.

## Consequences
- New dep: `cryptography`.
- Encryption is transparent to the rest of the app: `store.get_*` always returns plaintext.
- `enc:v1:` prefix makes seal/unseal idempotent and lets us rotate the scheme later (`v2:`).
- Per-stage model selection (ADR 0005) is the next part of milestone C and will extend the stored
  LLM settings with a `stage_models` map.
