# 19. Self-host packaging + CI (Milestone E)

Date: 2026-07-10 · Status: accepted · Roadmap: E

## Context
CodexMill needs to be something a non-technical writer can stand up with one command, and every
push should be gated the same way local commits are. Roadmap E: Docker, compose, CI, an AGPL source
link, release hygiene.

## Decision
- **Dockerfile** on `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` (multi-arch: amd64 + arm64).
  Dependencies install in a cached layer (`uv sync --no-install-project`) before the source, runs as
  a non-root `app` user, and serves `uvicorn codexmill.web.app:app` on `:8000`. `CODEXMILL_CONFIG_DIR`
  is `/data`, declared a `VOLUME`, so the config store + `bibles.db` persist. `.dockerignore` keeps
  the build context to just `pyproject`/`uv.lock`/`README`/`src`.
- **docker-compose.yml**: `docker compose up -d` → `http://localhost:8000`, a named `codexmill-data`
  volume, and commented env for `CODEXMILL_SECRET_KEY` (secrets-at-rest), a host Ollama, and the AGPL
  source URL.
- **CI** (`.github/workflows/ci.yml`, read by both Gitea Actions and GitHub): on push/PR, `uv sync`
  then the exact local gates — ruff check, ruff format --check, mypy --strict, pytest. Pytest is
  offline (fake backend), so CI makes **no** LLM/API calls.
- **AGPL source link** (§13): `CODEXMILL_SOURCE_URL` env, surfaced in `/api/me`, rendered as a
  "Source" link in the page footer next to the AGPL-3.0 license link (the license link is always
  shown). Operators who modify and serve CodexMill set this to their fork.

## Consequences
- The image is ~316 MB and verified on this aarch64 host: `docker build` (with the host network to
  bypass this LAN's TLS-inspecting bridge — a local quirk, not a Dockerfile issue), then a running
  container answered `/api/health`, `/api/me` (source_url from env), served the page, ran a fake
  generation, and confirmed the non-root `app` user.
- **CI needs a registered runner.** The workflow is correct and mirrors local gates, but whether it
  executes depends on a Gitea Actions runner being registered for the instance (infra follow-up); it
  will also work as-is if the project moves to GitHub.
- Publishing a multi-arch image to a registry (GHCR/Gitea packages) and cutting a tagged release are
  the remaining E tasks; they are outward-facing/registry decisions left for when the repo goes
  public.
