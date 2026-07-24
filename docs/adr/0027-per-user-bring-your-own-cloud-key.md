# 27. Per-user bring-your-own cloud key, with strict per-user isolation

Date: 2026-07-15 · Status: accepted

## Context
ADR 0025 shipped a shared-AI access model: a non-admin either generates with the server's own AI
(the admin's configured model + key) when the instance allows it, or not at all. Bring-your-own was
deferred with a specific reason — the server makes the AI calls, so a user's *local* endpoint
(`http://localhost:11434/v1`) is unreachable from the server and BYO "can't work remotely."

That reasoning holds only for a **local** endpoint. A **cloud frontier key** (OpenAI, Anthropic,
Google, OpenRouter, …) is remotely valid: the server can use a user-supplied key server-side to make
that user's calls, exactly as it uses the admin's shared key. On a shared or hosted instance, letting
each user bring their own frontier key is a real requirement — it lets people who don't want to
consume the operator's quota (or want a specific model) pay for their own usage.

Reintroducing a per-user key touches two things that must be gotten right:
1. **Key isolation.** One user must never be able to read another user's (or the admin's) key
   through the app. Same key value by coincidence is fine; *exposure* of one user's key to another
   is the failure.
2. **SSRF.** Today `base_url` has no validation, which is safe only because just admins can set it.
   The moment a non-admin can influence the endpoint, an arbitrary `base_url` becomes an SSRF vector
   into the host's network and a way to exfiltrate a key to an attacker host.

## Decision

### BYO cloud key for non-admins (local BYO stays out)
A user may store their own key for a **cloud frontier provider**; the server uses it for that user's
generations. Local-endpoint BYO remains unsupported: the server cannot reach a remote user's machine,
so it would work only for the operator, who already configures the shared AI. The reserved
`users.llm` JSON column (ADR 0026) now holds this per-user config.

### Provider allow-list — no arbitrary `base_url` from users
User BYO is **provider + key + model**, where the provider is chosen from a fixed server-side
allow-list (`web/providers.py`) and the `base_url` is derived from that list, never sent by the
client. A user therefore cannot point the server at an arbitrary URL, so BYO adds **zero** SSRF
surface. Admins keep arbitrary `base_url` (that is how a local Ollama is configured) — the admin is
already trusted to set the instance config outright.

This is paired with a separate hardening (see SECURITY.md): the per-request `base_url`/`api_key`/
`backend` overrides on the generation endpoints are honoured **only for admins**. Previously any
signed-in user could pass them, which allowed both SSRF and exfiltration of the shared key (supply a
`base_url` and no key → the server sends the shared key there). That fix is a prerequisite for
exposing an instance to untrusted users.

### Strict per-user isolation
- The key is sealed (Fernet, `enc:v1:`) into the caller's own `User.llm` row — never the shared
  config store. `unseal` returns empty on any failure, so a decryption problem reads as "no key,"
  never leaks the ciphertext as a bearer token.
- Every key operation is **session-scoped**: it acts on the authenticated caller's own row only. No
  endpoint accepts a target user id for a key operation, so there is no address by which user B can
  reach user A's key.
- The key is **write-only over the API**: `GET /api/me/llm` returns status (`has_key`, `provider`,
  `model`) and never the key value, not even to its owner. It is never logged.

### Choosing among the server's own models
Being allowed to use the server's AI should not mean being pinned to one model the admin picked. A
self-hosted instance usually fronts a local Ollama with several models, so a permitted user may
choose among them (`GET /api/me/server-models`, pinned via `POST /api/me/use-server-ai`), and the
choice is stored per user in the same `users.llm` column under a distinct `server_model` field with
no key — so it is never mistaken for a bring-your-own config.

That list is **curated** (`web/model_filter.py`). A model host commonly also carries embedding, OCR,
speech, reranking, moderation and image models, none of which can write prose; offering them is
noise, and picking one produces a baffling failure. Because the OpenAI-compatible `/v1/models`
endpoint returns ids with no capability metadata, the filter is a best-effort name match and is
deliberately **conservative**: it excludes only families that are unambiguously not text generators
and keeps anything unrecognised, since hiding a model someone wanted is worse than showing one extra.
A pinned model is validated against that same list, and only model *names* are ever returned — never
the server's `base_url` or key.

### Resolution precedence
For a generation, settings resolve as: **the caller's own key (if set) → the shared server AI (if the
instance allows it and the user's switch is on) → 403.** To fall back to the server AI, a user clears
their own key. `/api/me` exposes `ai_source` (`own` | `server` | `none`) so the active source is
observable.

### Trust boundary — stated, not hidden
Encryption-at-rest plus per-user isolation protects a key against a stolen database, a leaked backup,
and every other user of the instance. It does **not** hide the key from the instance's operator: the
server needs the plaintext at call time, and root controls both the encryption key and the code.
There is no zero-knowledge guarantee in a server-side-calls design, and we do not claim one. The UI
discloses this next to the key field ("encrypted and never shared with other users; the operator can
technically access it — only add a key if you trust this instance"). This is the same trust deal as
any hosted tool that holds your key.

## Consequences
- `users.llm` moves from reserved to used; no migration needed (the nullable column already exists).
- A per-user AI endpoint (`/api/me/llm`) is reintroduced — the one removed during the ADR 0025
  shared-AI work — this time provider-allow-listed, session-scoped, and write-only for the secret.
- New module `web/providers.py` (the allow-list). The non-admin app view gains a "Your AI" panel.
- The admin-only override lockdown is shipped first, as a standalone security fix.
- Threat model addition: BYO does not widen the SSRF surface (allow-list only); the residual key-
  exposure risk is operator-trust, which is documented rather than engineered away.
