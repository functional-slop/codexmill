# 5. Positioning vs prior art (StoryCraftr, AIStoryWriter)

Date: 2026-07-10 · Status: accepted

## Context
CodexMill is not first. Two open-source neighbors already exist and were studied before
building further:
- **StoryCraftr** (https://github.com/raestrada/storycraftr) — MIT, ~149★, beta. CLI + VSCode.
  À-la-carte commands (worldbuilding / outline / chapters / iterate), user-ordered. Consistency
  via **LangChain + a local Chroma RAG** over previously generated files (implicit, lossy, no
  voice guarantee). Backends: OpenAI / OpenRouter / Ollama.
- **AIStoryWriter** (https://github.com/datacrystals/AIStoryWriter) — AGPL-3.0, ~255★. Staged
  pipeline (outline → chapter-outline → chapter → revision) with **per-stage model selection**.
  Backends: Ollama / Gemini / OpenRouter via a `provider://model@host` string. **No documented
  long-form memory** — its own README admits repetition, weak chapter transitions, pacing drift.

## Decision
Position CodexMill on what is actually defensible, and stop pitching what is table stakes.

**NOT a differentiator (both competitors already do it): "runs local models / backend-agnostic."**
Keep it (it matters for reach) but never lead with it. Our env contract stays deliberately
simpler than AIStoryWriter's `provider://model@host` DSL and StoryCraftr's per-provider config.

**Real differentiators — lean in:**
1. **Pydantic-validated hand-offs between stages.** Neither validates inter-stage contracts.
   This is the primary moat against drift.
2. **Explicit voice sheets + rolling summary** as the consistency mechanism, threaded into every
   downstream prompt — auditable, unlike StoryCraftr's RAG or AIStoryWriter's absent memory.
3. **Cohesive, human-editable story-bible bundle + KDP metadata.** Neither ships publishing
   metadata; both produce looser output.

**Borrow (licenses permit — StoryCraftr MIT, AIStoryWriter AGPL, compatible with our AGPL):**
- Per-stage model selection (AIStoryWriter): each stage may override model/endpoint for
  cost/quality routing. Default remains one model.
- RAG-over-own-output (StoryCraftr): OPTIONAL secondary backstop to the rolling summary for very
  long works. Voice sheet + summary stay primary. Do not build until a real length need appears.
- Reindex/re-ingest on manual edits (StoryCraftr `reload-files`): since our bundle is
  human-editable, re-read edits before the next stage rather than run on stale state.
- Multi-language as first-class config: cheap, later.

## Consequences
- The chapters stage MUST thread the rolling summary + voice sheets into *every* chapter prompt,
  and this must be verified end-to-end (not per-stage). Avoiding AIStoryWriter's no-memory trap
  is a correctness requirement, not a nicety.
- Keep the pipeline tight and opinionated. Both competitors show the failure of scope sprawl
  (StoryCraftr stuck at beta; AIStoryWriter's core quality issues open across 284 commits).
  Ship a reliable bible, not a broad half-working toolbox.
- Make state a visible artifact (the bundle), never a hidden vector store.
- Per-stage model selection and optional RAG are recorded here so they are not "discovered" and
  half-built later; they are deliberate, deferred choices.
