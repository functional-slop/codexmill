# 20. Front-end redesign ("manuscript" identity, light+dark)

Date: 2026-07-11 · Status: accepted

## Context
The original UI worked but looked generic. A dedicated design pass (handed the sanitized codebase +
a brief) returned drop-in production files: a cohesive "manuscript" identity with twin light/dark
themes (warm parchment/gilt vs. candlelit study), a proper result-document view (sticky toolbar,
Contents TOC, document sheet), a streaming stage-checklist, and a redesigned Settings page, all
in the repo's own buildless style (static HTML + one CSS file + vanilla JS), wired to the unchanged
API.

## Decision
Adopt the redesigned `index.html`, `admin.html`, `app.css` verbatim (presentation only — no backend
or API change), after an audit + three fixes:
- **Self-hosted fonts** (the one missing asset): shipped Newsreader + Archivo variable `.woff2` under
  `static/fonts/` with their OFL licenses. `@font-face` was already in the CSS; system fonts are the
  fallback. No CDN — satisfies the offline constraint and the app's CSP (`connect/font-src 'self'`).
- **Re-added "See a sample"** — the redesign had dropped the link that previews the bundled
  `sample.md` for a visitor with no engine configured.
- **Re-added the `worldbuilding` per-stage model override** in Settings — the redesign's per-stage
  map had omitted it (a real LLM stage).

Theme is driven by CSS custom properties with a top-bar toggle persisted to `localStorage['cm-theme']`
and a tiny inline `<head>` script that sets `data-theme` before first paint (FOUC-safe; allowed by
the `script-src 'unsafe-inline'` in our CSP).

## Consequences
- Verified by running the app: index/admin/CSS/fonts/sample all serve, the SSE generate stream emits
  all 7 stages + done, the CSP is intact, and 58 tests pass. Full visual QA is the design session's
  rendered screenshots (both themes) plus the live demo.
- The dependency-free Markdown renderer (with HTML-escaping) is preserved; the result view adds a
  TOC built from `##` headings, still dependency-free.
- API contract unchanged; all prior features carried over (series mode, streaming, exports
  docx/obsidian/copy/print, stage + book regenerate, both libraries, `/api/me` CTAs, source footer,
  OIDC/setup-token admin).
