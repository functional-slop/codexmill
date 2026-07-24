# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-07-24

### Security (multi-user AI isolation)
- **A non-admin can never spend a paid server key.** If the shared "server AI" carries an API key
  (a paid cloud endpoint), non-admins are blocked from using it unless the operator explicitly opts
  in (`allow_shared_paid_key`, default off); a keyless local server AI stays freely shareable. This
  closes a path where users could generate on the operator's own cloud quota.
- **An admin's personal AI is separate from the server's.** Admins get the same private "Your AI"
  panel as everyone; a key set there is the admin's own (isolated), and no longer doubles as the
  shared server configuration. Admin Settings clearly labels the "Server AI" as the shared one and
  warns before a paid server key is shared.

### Fixed
- **Character names are grounded in the world, with a cliché backstop.** The character stage now
  receives the worldbuilding (cultures + geography) so names fit the setting, and the premise stays
  role-based (no name it later contradicts). Because models lean hard on a few default names anyway,
  an over-used name in the generated cast triggers exactly one reroll with those names rejected.
- **Bring-your-own generation reason is honest.** A provider 404 now reports the provider's actual
  message (e.g. "this model is no longer available to new users") instead of "check the model name",
  and the model dropdown lists what the key can really use. Structured output degrades to prompt-only
  on any endpoint that rejects it, so a full generation works on providers like Gemini.
- **UI consistency:** the "Your AI" nav item highlights on its own screen, appears on the Settings
  page too, and warning/error colors adapt to light + dark themes.

### Added

#### Earlier in this cycle
- **Multi-user, roles, and admin controls (ADR 0025/0026).** Every account (local or OIDC) is now a
  DB row with a role (root/admin/user), an active flag, and per-user permissions — including
  `use_server_engine` (per-user access to the shared server AI) and a per-user generation quota.
  A **Users** admin panel lists/creates/disables/deletes accounts, changes roles, and resets
  passwords, with guardrails (non-root can't touch root; you can't remove the last root or lock
  yourself out). Ownership, admin checks, and session revocation all key on a stable user id.
- **Persistence on SQLAlchemy + Alembic.** SQLite stays the zero-config default; Postgres is opt-in
  via `CODEXMILL_DATABASE_URL` (`postgresql+psycopg://…`). Schema is migration-managed.
- **Spec-correct OIDC + provisioning.** PKCE (S256), identity keyed on `(iss, sub)`, verified-email
  or username linking that refuses to bind to a privileged or cross-issuer account, optional
  auto-register, and IdP group→role mapping. Configurable sign-in methods (local / OIDC / both).
- **Shared-AI access control.** A global toggle decides whether signed-in users may generate with
  the server's AI, and a per-user switch (shown when the global one is on) picks exactly who. Off
  means only admins can generate. Simple and clear: the server provides the AI; you choose who uses
  it.
- **Bring your own cloud key (ADR 0027).** A signed-in user can add their own key for a frontier
  provider (OpenAI, Anthropic, Gemini, Groq, Mistral, OpenRouter, xAI) from a "Your AI" panel and
  generate on their own account. Provider choice is a fixed allow-list, so the base URL is set
  server-side and a user can't point the server at an arbitrary endpoint. A personal key takes
  precedence over the shared server AI; clearing it falls back. Keys are sealed at rest, scoped to
  the owner, and never returned to any client (not even the owner), so one user can never read
  another's. Local-Ollama BYO stays out (the server can't reach a remote user's machine).
- **Offline recovery CLI:** `python -m codexmill.auth_reset` resets a password or sets a temporary one.
- **Per-item generation time in the library.** Each saved bible/series now records the wall-clock
  seconds it took to generate (a regenerate adds its time), shown beside the token cost in the
  library rows ("genre · date · N tokens · 1m 23s"). New `gen_seconds` column (migrated).

- **Large models are flagged as slow in the picker.** Each option shows its size, and picking a
  heavy one explains what to expect: slow generation across many back-to-back stages, and queueing
  behind anyone else using the same model (a local host typically serves one request at a time per
  model). Sizes come from an Ollama host's native model list; hosts that don't report a size simply
  get no warning. Size is a rough proxy — a large mixture-of-experts can still be fast — so the
  threshold only calls out genuinely heavy models, and nothing is ever blocked.
- **Users can pick among the server's models, from a curated list.** If you're allowed to use the
  server's AI (typically a local Ollama), "Your AI" now offers its models rather than pinning you to
  the admin's default, and remembers your pick. The list is curated: models that can't write prose
  (embeddings, OCR, speech, rerankers, moderation classifiers, image models) are filtered out, and
  HuggingFace-style ids are shown readably (`hf.co/TheDrummer/Anubis-70B-v1.2-GGUF:Q4_K_M` →
  "Anubis-70B-v1.2 (Q4_K_M)"). The filter is deliberately conservative — anything unrecognised is
  still offered. Only model names are exposed; never the server's URL or key.
- **Every bible records which model made it.** The model is stored per item and shown while
  generating, on the finished bible, and in the library row next to the token count (a token count
  can't be read without knowing what spent it). "Your AI" gained a **usage breakdown by model** for
  the signed-in user: tokens and item count per model, plus a total. Items generated before this
  change show no model rather than a guess.

### Fixed (bring-your-own key)
- **"Test" now actually generates** instead of only listing models. A key can often list models it
  can't generate with (wrong tier, model not enabled for the project, quota), which passed the old
  test and then failed at generation. Test now does a tiny real generation with the chosen model and
  surfaces the provider's actual error.
- **Model dropdown for your own key.** Each provider offers common models to pick from (still
  free-text, so you can type any), instead of a single pre-filled default. Helps when a key can't
  use the newest model.
- **Gemini's default model is now `gemini-2.0-flash`** (was `gemini-2.5-flash`), the more
  broadly-available choice for a fresh key.
- **Cost disclaimer** added to the bring-your-own panel: generation runs on your own provider
  account, every stage is a separate call, and the instance isn't responsible for your charges.
- **One unified AI panel (was two confusing sections).** Choosing an AI is now a single admin-style
  form: one "AI engine" dropdown lists this server's AI (when the admin shares it) alongside every
  frontier provider. Pick the server → a model dropdown of its local models, no key. Pick a provider
  → an API-key box, a model dropdown, and the cost note appear. The separate "use this server's AI"
  card is gone.
- **The model dropdown now shows the models your key can actually use.** When you enter your key,
  the panel lists the models that key can list (server-side, against the provider's fixed endpoint,
  using only the key you just typed) instead of a short hard-coded set. You can still type any model.
- **Full generation now works with a Gemini key, not just "Surprise me".** Gemini's OpenAI-compatible
  endpoint rejects the nested JSON Schemas the multi-stage bible uses (while the single-call "Surprise
  me" worked), and the app didn't fall back. Any endpoint that rejects structured output now degrades
  to the portable prompt-only path for the rest of the run, so a bring-your-own Gemini key completes a
  full bible. (The model field is also a real dropdown with autofill disabled, so a browser password
  manager can no longer hijack it.)

### Added (operator)
- **`CODEXMILL_MODEL_DENYLIST`** (comma-separated substrings) hides specific server models from the
  picker, for models that technically generate but produce poor output on this pipeline (e.g. a base
  model that ignores the injected premise).

### Fixed (generation)
- **Generation now works with chatty and roleplay-tuned models, not just the most obedient ones.**
  The pipeline only *asked* for JSON in the prompt; a model that ignored that and replied
  conversationally never validated and failed the whole run after burning its retries. Where the
  endpoint supports structured outputs (Ollama, OpenAI, …), decoding is now CONSTRAINED to the
  target JSON Schema, so the model can't wander off into prose. Endpoints that don't support it
  degrade to the previous prompt-only behaviour, so nothing regresses. (Verified end to end: a full
  generation on a 35B model that previously failed now completes.)

### Fixed (security)
- **AI endpoint overrides are now admin-only.** The generation endpoints accepted `base_url` /
  `api_key` overrides from any signed-in user; a non-admin could point the server at an arbitrary
  host and, by omitting a key, make it send the shared server key there (SSRF + key exfiltration).
  Overrides are now honoured only for admins.

### Fixed (AI setup)
- **A new user is asked which AI to use instead of silently borrowing the server's.** First visit now
  shows a one-time picker — this server's AI (named) or your own provider key — and either choice is
  remembered. Non-admins also get a persistent "Your AI" menu entry to switch later; previously the
  only AI settings lived behind the admin-only Settings page.
- **"AI ready" no longer appears when the instance has no AI.** Readiness was computed from
  *permission* to use a shared AI (on by default) without checking one was configured, so users were
  shown a working form that failed against the built-in localhost default. Generation now returns a
  clear "this server doesn't have an AI set up yet" instead of an obscure connection error.

### Fixed (sign-in / admin)
- **OIDC sign-in errors no longer crash.** If the identity provider returns an error, or the state /
  token exchange fails, the callback now redirects back to the login screen with a short reason
  instead of returning a 500. (An OIDC provider must allow the `authorization_code` grant for sign-in
  to work — a provider configured without it will reject the request as malformed.)
- **Regular users can no longer see the admin Settings.** The Settings page and its nav link are now
  gated on the signed-in user's role; a non-admin is sent back to the app and never sees the engine,
  access, or user-management panels. (The admin APIs already enforced this server-side.)
- **Sign-in checkboxes and the role dropdown render correctly.** Checkboxes now match the theme
  instead of the browser default, the role selector no longer clips its text, and a stylesheet
  cache-bust ensures UI updates show without a manual hard-refresh.
- **SSO-only instances hide the password form.** When password login is turned off, the sign-in
  screen shows only the single-sign-on button.

### Fixed (drafting prompts)
- **Worldbuilding is now in the copy-paste chapter prompts.** The Stage-5 writing prompts carried
  premise + character voices + story-so-far + scene beats but NOT the worldbuilding, so all the
  setting the bible generated was wasted when you pasted a chapter prompt into your AI. Each prompt
  now embeds a compact "Setting & rules" brief. (Regenerating worldbuilding also re-runs the prompts.)

### Fixed (Settings / AI-Engine panel)
- **Model dropdown showed only a few of the engine's models.** A `<datalist>` filters its options by
  the field's current text, and the Model field is pre-filled with the provider default — so most
  models were hidden. Replaced it with a real `<select>` that lists every model the engine reports
  (the text field stays for typing a custom/unlisted model). Same fix restores model listing for a
  local Ollama reached by IP.
- **Failed model listing was silent.** `loadModels()` now surfaces a hint beside the Model field when
  the engine can't be reached or lists nothing, instead of swallowing the error.
- **Switching providers wiped the previous one's key/URL.** Per-provider fields are now remembered in
  memory for the session, so a custom Ollama IP (etc.) survives switching away and back.
- **Chrome autofilled saved credentials into the Model/Base URL fields.** Added autofill guards
  (`autocomplete`, `data-1p-ignore`/`data-lpignore`, `new-password` on the key field).

### Fixed (drafting workflow)
- **Writing prompts no longer ask for an impossible single-shot chapter.** They said "Write ~N words"
  where N = target_words/chapters (up to ~10k), which no model drafts well in one reply. The prompt
  now instructs drafting **scene by scene** (one scene per reply, ~600-1500 words each, "continue"
  for the next), using the ordered scene beats we already emit; the chapter total is shown as pacing
  context. The rolling "story so far" recap + exact voice sheets (the continuity mechanism) are
  unchanged — an independent AI review confirmed that part is the right approach.
- **The Length preset now sets a matching word target** (short story ~7.5k, novella ~40k, novel ~80k,
  epic ~120k) instead of leaving every size at a novel's 80k — so a "short story" is actually short.

### Security — full audit fixes (ADR 0024, `docs/SECURITY.md`)
Four independent adversarial reviews. Core confirmed clean (no IDOR/SQLi/XSS/traversal). Fixed:
- **Critical:** `/api/auth/setup` was gated on "has a local account" not "is unconfigured", so an
  OIDC-configured instance with no local account let any anonymous visitor create the admin. Now
  closed unless genuinely first-run; requires `X-Setup-Token` when `CODEXMILL_SETUP_TOKEN` is set.
- **High:** the config store briefly wrote the session-signing secret world-readable (chmod after
  write). Now written 0600-from-creation atomically; same for `secret.key`.
- **High:** "Test connection" reused the stored API key against a caller-supplied URL (exfiltration).
  It no longer sends the stored key to a different `base_url`.
- **Med/High:** logout now revokes an already-issued cookie (per-identity session epoch); session
  lifetime capped and configurable.
- **Low:** disabled unauthenticated OpenAPI/`/docs`; `delete` is kind-scoped; login timing-oracle and
  brute-force throttle (shipped earlier in the auth work) verified.
- Deploy: container deps pinned to exact versions (no resolve-to-latest on restart).

### Security (BREAKING — ADR 0024)
- **Login is now required.** A local admin account (username + password) is created as **step 1 of
  first-run onboarding**, and every route needs a session afterwards. Previously the app had no login
  at all: OIDC was optional and off by default, so an unconfigured instance had an **open admin
  surface** (anyone who could reach it could read the library or overwrite the stored API key). A fresh
  instance is now **closed** — only `/api/me` and `/api/auth/setup` respond until an account exists,
  and setup refuses once one does, so it can't be used to take over a configured instance.
  Passwords are hashed with **argon2id** (`argon2-cffi`, the OWASP first-choice KDF), salted per
  password, and transparently re-hashed on login when parameters change. OIDC still composes on top
  for SSO on a shared instance; the local account is the always-there floor.
- **Secrets are always encrypted at rest.** `crypto.seal()` used to be a no-op unless the operator
  remembered `CODEXMILL_SECRET_KEY`, so the stored LLM API key and OIDC client secret sat in
  `codexmill.json` in **plaintext on every default deploy**. The key now resolves env → an
  auto-generated, persisted `secret.key` (chmod 600) in the config dir, so encryption can no longer be
  lost by forgetting an env var.
- Existing instances: an instance with no account shows the create-admin step on next load. Bibles
  saved before auth existed (owner `local`) are not migrated — a bible is an artifact you **export**;
  the library is a convenience, not a system of record.

### Added (UX polish, 2026-07-12)
- **New Bible form now survives navigation**: popping over to Settings and back no longer wipes your
  inputs (including "Surprise me" values). Persisted in `sessionStorage` until the tab closes or a
  generation succeeds.
- **Live token count + elapsed timer during generation**: the streaming panel shows a ⏱ elapsed timer
  and a running token total (fed by the per-stage SSE events) so it's clearly not frozen. Reworded the
  time estimate away from the too-low "30–120 seconds".
- **Per-item token cost in the library**: each saved bible/series shows how many tokens it took
  ("genre · date · N tokens"); a regenerate adds its cost to the item's running total.
- **Fixed** an empty token-meter pill rendering as a hollow bubble when there was nothing to show.

### Added (Milestone F — cost controls)
- **Per-user generation quotas** (ADR 0022): an opt-in cap of N generations per rolling window per
  person, so an operator who exposes CodexMill to others can't have their paid API key run dry. Off by
  default (`max_generations = 0` = unlimited), so single-container/personal instances are unaffected.
  Configured in the `/admin` **Access & Login** tab (or `CODEXMILL_MAX_GENERATIONS` /
  `CODEXMILL_RATE_WINDOW_HOURS` env for headless). Enforced on every LLM-invoking endpoint (generate,
  stream, series, and both regenerates); over-limit returns HTTP 429 with a plain message +
  `Retry-After`. Owner = session email (OIDC on) else `local`; a `rate_events` table in the library DB
  counts the attempt (a started run spends tokens even if it later fails). Input-shape 422s reject
  before the gate, so a malformed request never burns a slot.
- **Per-generation usage meter** (ADR 0021): every generation now reports how many tokens it used
  (prompt / completion / total + call count), so a friend running the beta on a paid key can see a
  run's cost instead of finding out from the bill. Measured on the backend from the OpenAI SDK's
  `usage` (retries counted, since each is billed); threaded out through `GenerateResponse.usage` and
  the SSE `done` events for single-book **and** series (generate, stream, regenerate). The web UI
  shows a "· N tokens" note next to the word count; loading a saved bible shows none (costs nothing).
  Tokens, not dollars (dollar estimation needs a per-model price table and is meaningless for free
  Ollama — deferred). Offline `fake` backend synthesizes a deterministic placeholder tally.

### Added (beta readiness)
- Installable **PWA**: `manifest.webmanifest` + service worker (`sw.js`, offline app-shell cache;
  never caches `/api`) + app icons (192/512, apple-touch 180, maskable) — home-screen install on
  iOS/Android/desktop, no app store.
- **Mobile/responsive pass**: verified at 390px — killed horizontal overflow (the fixed candle-glow),
  added a phone breakpoint (tighter paddings/type, wrapping toolbar), fixed the Settings tab rail.
- **"Surprise me"** (`POST /api/surprise`): the AI invents a one-off genre/idea/tropes to prefill the
  form (not from a list). Restored the **Themes & tropes** field the redesign had dropped (it was
  sending empty tropes).
- **Model field is a live dropdown** from the connected engine's `/models` (Ollama/Gemini/OpenRouter
  round-tripped; OpenAI/Groq/Anthropic endpoints confirmed). Provider defaults audited; Gemini →
  `gemini-2.5-flash`. Dropped blanket free/paid provider labels.
- **Focused first-run flow**: onboarding is its own view (nav hidden); "Set up your engine" opens a
  focused `/admin?onboarding=1` (only AI Engine tab, "All set →" exit); "See a sample" from onboarding
  shows just the document with "← Back to setup". Real hover tooltips replace flaky native `title`.
- First-run onboarding: when no AI engine is configured, the home page shows a "Welcome to
  CodexMill" card explaining what it does and the two steps (connect an engine → generate), with
  a set-up button and links to how-it-works / a sample. (OIDC is the opt-in SSO path for shared
  multi-user instances.)
- Seamless local Ollama in a container: `CODEXMILL_OLLAMA_URL` (surfaced via `/api/me`) pre-fills
  the Settings Ollama URL, so a container that sets it to `http://host.docker.internal:11434/v1`
  makes local Ollama work without the user editing anything.
- "How to use this bible" explainer: a home-page "How CodexMill works" note and a callout at
  the top of every result spelling out the paste-one-chapter-at-a-time workflow (the Writing
  Prompts are pasted into any AI per chapter, not the whole doc at once).
- Optional "Send feedback" mailto link in the footer, address via `CODEXMILL_FEEDBACK_EMAIL`
  (surfaced through `/api/me`; hidden when unset).
- Verified the image is genuinely multi-arch: built and ran **linux/amd64** (under emulation:
  `/api/health` + a fake generation both 200) in addition to arm64, so it runs on Intel/AMD and
  Apple-Silicon machines. compose reaches a host Ollama on Linux too (`host.docker.internal`).

### Changed
- Front-end redesign (ADR 0020): a cohesive "manuscript" identity with twin light/dark themes (top-bar
  toggle, FOUC-safe), a proper result-document view (sticky toolbar + Contents TOC + document sheet),
  a streaming stage-checklist, and a redesigned Settings page. Drop-in from a design pass, buildless
  (static HTML + one CSS + vanilla JS), API unchanged. Self-hosted Newsreader + Archivo variable fonts
  under `static/fonts/` (OFL) — no CDN, CSP-clean, system-font fallback. Audited for parity; re-added
  "See a sample" and the worldbuilding per-stage model override that the redesign had dropped.

### Hardened (production readiness)
- Bounded all request inputs (Spec/SeriesSpec) so the API can't be cost/DoS-bombed — out-of-bounds
  requests are `422` before any model call (chapters 1-60, series books 1-12, chapters-per-book
  1-40, string-length caps).
- LLM calls now have a per-call timeout (`CODEXMILL_TIMEOUT`, default 120s) and one retry; every
  engine failure (timeout / bad key / unknown model / rate limit / unreachable) becomes a
  plain-language `BackendError` the user can act on, instead of a hang or a raw traceback.
- SQLite runs in WAL mode with a busy timeout, so concurrent generations don't hit "database is
  locked".
- Security headers on every response (`X-Content-Type-Options`, `X-Frame-Options: DENY`,
  `Referrer-Policy`, a strict `Content-Security-Policy` matching the offline-only design); session
  cookie is `SameSite=lax` (CSRF-safe) and becomes `Secure` when `CODEXMILL_HTTPS_ONLY` is set.
- Added `docs/PRODUCTION.md` — deployment/hardening guide (what's covered, what you must set,
  what's not yet covered, e.g. rate limiting).

### Added
- Self-host packaging + CI (Milestone E, ADR 0019): a multi-arch **Dockerfile** (amd64 + arm64, uv
  base, non-root `app` user, `CODEXMILL_CONFIG_DIR=/data` volume) and **docker-compose.yml**
  (`docker compose up -d` → :8000, named data volume); a **CI workflow** (`.github/workflows/ci.yml`,
  read by both Gitea Actions and GitHub) running the local gates (ruff, mypy --strict, pytest — all
  offline, no API calls); and an **AGPL-3.0 "Source" link** in the page footer (`CODEXMILL_SOURCE_URL`
  via `/api/me`). Image verified building + running on aarch64. README gains a self-host section.
- Series output parity (ADR 0018): series-level export — `GET /api/series/{id}/export?format=docx|
  obsidian` (a Word doc with each book on its own page; an Obsidian/Scrivener .zip with the shared
  world/cast at the root and a subfolder per book) — and regenerate-a-book — `POST /api/series/{id}/
  regenerate {book}` re-runs one book with the shared world+cast and the prior-books recap, in place.
  The web toolbar exports a series and offers a "Regenerate book" control.

### Fixed
- Series naming (ADR 0018): the recurring cast is now generated before the series plan and passed
  into it, so the plan's book lineup uses the real character names instead of a placeholder the cast
  could contradict. The base premise seeding world+cast comes from `series_premise_hint`.

### Added
- Series web surface (ADR 0018): `/api/series` CRUD + `POST /api/series/stream` (SSE per-book
  progress), backed by a `kind` column in `web/library.py` (migrated in place) so books and series
  share the table but are kind-isolated (`/api/bibles` and `/api/series` never bleed together). The
  web UI gains a "Single book / Series" mode toggle (Books + chapters-per-book) and a "My series"
  list; export/regenerate stay single-book only. Live-verified end-to-end.
- Series / continuity engine (ADR 0018, competitive-parity #2): multi-book series generation where
  worldbuilding + the recurring cast are generated **once** and shared by every book (continuity by
  construction, they can't drift), and each book advances the arc seeded with a "story so far" recap
  of the prior books. New `SeriesSpec`/`SeriesPlan`/`SeriesBible` schemas, `stages/series_plan.py`,
  `series.py` (`build_series` + streaming `build_series_iter`), `render_series` (shared world + cast
  shown once, then each book's book-specific sections). New `codexmill series --spec ... --out ...`
  CLI + `examples/series.yaml`; the fake backend honors the requested book count. Web API/UI to come.
- Exports (roadmap D, ADR 0016): download a saved bible as a Word **.docx** (built directly from the
  schema with python-docx, real Word headings/lists) or an Obsidian/Scrivener **.zip** (a folder of
  per-section Markdown), via `GET /api/bibles/{id}/export?format=docx|obsidian`. `render.py` refactored
  so `bible_sections()` is the single source of section content, shared by the Markdown bundle and the
  Obsidian export. Toolbar gains Word / Obsidian buttons (disabled for the id-less sample view).
- Regenerate a single stage (roadmap D, ADR 0017): `POST /api/bibles/{id}/regenerate {stage}` re-runs
  the target stage plus every stage that depends on it (worldbuilding/KDP are leaves; characters
  cascades to structure/chapters/prompts; premise redoes all), reusing upstream stages so the bible
  stays consistent, and patches the stored row in place (`Library.update`, same id). The toolbar gains
  a stage picker + Regenerate button (shown when a saved bible is open and an engine is configured).
- Formatted output + copy/print (roadmap D): the generated bible now renders as a formatted
  "manuscript" document (dependency-free Markdown renderer: headings, lists, bold, fenced prompt
  cards) instead of a raw text block, with a toolbar for Copy (Markdown) and Print / Save-as-PDF
  (a print stylesheet isolates the document). Renderer verified against the real sample.
- Worldbuilding stage (ADR 0015, competitive-parity #1): a 7th pipeline stage producing history /
  geography / cultures / factions / systems (magic, tech, or governing rules — genre-general),
  rendered as a "## Worldbuilding" section. Closes the one gap the only viewable rival (StoryCraftr)
  had over us. Prose drafting was considered and DECLINED (stays a planner, not a ghostwriter).

- Project scaffold: AGPL-3.0 license, `uv`/`pyproject` build, `ruff` + `mypy` (strict) +
  `pytest`, `pre-commit` enforcement, Gitea repo.
- Session-continuity system: `docs/STATE.md`, `docs/CONTINUATION.md`, ADRs, `docs/ORG.md`.
- Backend-agnostic LLM layer (`CODEXMILL_*` env config) with an offline fake backend.
- Working vertical slice: `codexmill generate` runs premise → characters → structure and
  renders a Markdown story-bible bundle.
- CLI smoke test that drives the real entrypoint offline.
- Chapter-breakdown stage (`stages/chapters.py`): expands each outline chapter one at a time,
  threading character voice sheets + a rolling summary into every prompt (ADR 0005). Threading
  is verified end-to-end in `tests/test_chapters.py`.
- Writing-prompt stage (`stages/prompts.py`): deterministic per-chapter, copy-paste drafting
  prompts assembled from scene beats + voice sheets + rolling story-so-far (ADR 0006). No LLM
  call, so prompts are reproducible; threading verified in `tests/test_prompts.py`.
- KDP-metadata stage (`stages/metadata.py`): keywords, category paths, back-cover blurb, and
  short description from the premise (ADR 0005 differentiator). Completes the v1 6-stage core
  pipeline (premise → characters → structure → chapters → writing prompts → KDP metadata).
- Web UI (ADR 0007): `codexmill serve` runs a FastAPI app serving one self-contained page (form
  + engine picker + generate/preview/download). Bring-your-own-key, never stored; browser talks
  only to the server. Shared pipeline core; `/api/generate` tested offline in `tests/test_web.py`.
- Backend override for the web request path (`Settings.from_overrides`) and a shared
  `render.slugify` used by both CLI and API.
- Optional OIDC login (ADR 0008): off unless configured; any provider via discovery URL. When on,
  gates `/api/generate` (401 for anon) while the page stays reachable with a Sign-in link. Authlib
  + Starlette sessions; routes `/auth/{login,callback,logout}` + `/api/me`.
- GUI-configurable OIDC + persistent config store (ADR 0009): configure OIDC in the `/admin` page
  (issuer/client-id/secret/admin-emails, with a "Test connection" discovery probe) — no env, no
  restart. New `web/store.py` (atomic JSON, chmod600, `~/.local/state/codexmill` default) is the
  app's first persistent state; holds OIDC config, admin allowlist, and a stable session secret.
  Store-first-then-env resolution; bootstrap-safe admin (`CODEXMILL_SETUP_TOKEN` break-glass).
  Verified live end-to-end against a real OIDC provider.
- Persistent bible library (ADR 0010, roadmap milestone A): SQLite `web/library.py`; every
  generate auto-saves; `GET /api/bibles`, `GET /api/bibles/{id}`, `DELETE /api/bibles/{id}`; a
  "My bibles" list on the page; per-owner isolation (session email when OIDC on, else `local`).
- `docs/ROADMAP.md`: the sequenced program (A-H) to a polished self-hostable / desktop app.
- Streaming progress (ADR 0011, roadmap milestone B): `pipeline.build_iter` yields per-stage
  events; `POST /api/generate/stream` (SSE) streams `{stage,index,total}` then `{done,id,markdown}`;
  the page shows live "Generating… chapters (4/6)" instead of a blind spinner. `pipeline.build` and
  the plain `/api/generate` are unchanged for CLI/programmatic use.
- Saved server-side LLM settings + secrets-at-rest (ADR 0012, roadmap C pt.1): default engine/
  model/key stored in the config store and managed at `/admin` (`GET/PUT /api/admin/llm` + a
  `/test` that lists the endpoint's models). Resolution precedence is request > saved > env; the
  form offers a one-click "Server default" engine so users stop pasting keys. Stored secrets (LLM
  key + OIDC client secret) are Fernet-encrypted at rest when `CODEXMILL_SECRET_KEY` is set
  (`web/crypto.py`, `enc:v1:` prefix); the API never returns the key, only `has_key`.
- Per-stage model selection (ADR 0005, roadmap C pt.2): each pipeline stage can use a different
  model (strong for structure, cheap/local for the bulk chapters). Optional `model` param on
  `Backend.generate` + a `bind_model` wrapper; `stage_models` threaded through the pipeline, stored
  in the LLM settings, and editable per stage in `/admin`. A request-level model overrides uniformly.
- Dropped the fake "offline demo" engine (ADR 0014): an offline demo can't generate without an AI
  — it only returned hardcoded placeholder text that ignored the user's input. Removed from the
  UI; `FakeBackend` is now a test-only fixture. Instead, a real pre-generated `sample.md` (6-chapter
  epic fantasy, ~11k words) is bundled and shown via a "See a sample" link; with no engine
  configured the main page nudges to Settings rather than faking a generation.
- UX/IA redesign (ADR 0013, roadmap D pass): the main page is now just the story form with
  essentials visible and the rest under an "Advanced options" disclosure; engine/API setup moved
  off it into a **tabbed Settings page** (AI Engine / Access & Login / About)
  with a **provider dropdown** (Ollama/Gemini/OpenAI/Groq/OpenRouter/Anthropic/Custom, free/paid
  tagged, auto-filled base URL + "Get a key" links) and a "← Back to app" link. Every field has a
  hover tooltip. Romance-specific `heat` renamed to a genre-neutral **`maturity`** content rating
  (All ages/Teen/Mature/Explicit) through the schema, prompts, and examples.
- Novel-scale honesty: web form defaults to 24 chapters (with guidance), the result shows an
  approximate word count, the offline fake backend now honors the requested chapter count (was
  hardcoded to 3), and `examples/novel.yaml` (24 ch) joins the quick `minimal.yaml`. Measured: a
  real 24-chapter bible is ~58k words, exceeding the paid product's 35-45k claim.
