# 13. UX/IA redesign: simple main form + tabbed Settings

Date: 2026-07-10 · Status: accepted · Roadmap milestone D (UX pass)

## Context
The first UI put everything on one screen (engine/API/key pickers next to the story form), used
romance jargon ("heat") for a general-purpose tool, had no tooltips, and stacked all settings in
one column. Feedback + a scan of UX guidance (progressive disclosure, IA, provider-selection
patterns, plain-language microcopy) drove a redesign.

## Decision
- **Main page = the task only.** Genre, idea, tropes, and a Length preset are visible; POV,
  content rating, exact chapter count, target words, and structure live under an "Advanced
  options" `<details>` (progressive disclosure). Engine/API config is *removed* from the main
  page — it just shows which engine is active with a link to Settings, and defaults to the
  offline demo when none is set.
- **Settings = a tabbed page**: a category nav with
  **AI Engine**, **Access & Login**, and **About** sections, plus a "← Back to app" link. AI
  Engine has a **provider dropdown** (Offline demo, Local Ollama, Gemini, OpenAI, Groq,
  OpenRouter, Anthropic, Custom) tagged free/paid, which auto-fills the base URL/model, shows the
  key field only when needed, and offers a "Get a key ↗" link. Per-stage models and OIDC live
  in their own (advanced) areas.
- **"heat" → "maturity" (Content rating).** Genre-neutral (All ages / Teen / Mature / Explicit),
  renamed through the schema/prompts/examples, not just relabeled. Applies to violence, language,
  and any romance.
- **Tooltips everywhere.** Every field/label has a `title` hover tooltip plus an "ⓘ" affordance;
  labels are real labels (not placeholder-only), and microcopy is plain language.

## Consequences
- The main screen is far lighter; setup happens once in Settings, then generation is one click.
- Provider presets lower the barrier for non-technical users (pick a name, paste a key, done).
- The schema field is now `maturity`; any external caller using `heat` must update.
- Output is still a raw `<pre>` block — formatted HTML rendering + exports remain in milestone D.

Sources reviewed: progressive disclosure (LogRocket, IxDF, UXPin), IA/navigation (Pencil&Paper),
provider-selection (LibreChat/OpenRouter docs), microcopy (Justinmind, NN/g).
