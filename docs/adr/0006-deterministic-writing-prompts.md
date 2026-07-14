# 6. Per-chapter writing prompts are assembled, not generated

Date: 2026-07-10 · Status: accepted

## Context
The writing-prompt stage produces the ready-to-paste instruction a user hands to an LLM to draft
each chapter. Everything that goes into it is already known: the chapter's scene beats, the
character voice sheets, the story-so-far, and the spec (POV, heat, words per chapter).

## Decision
Assemble each writing prompt deterministically from those pieces. No LLM call in this stage.

## Consequences
- The prompts are fully reproducible and cannot themselves drift or hallucinate — a quality the
  paid "voice-calibrated prompt" products cannot guarantee.
- Free: one of the longest sections of the bundle costs zero tokens.
- Threading (voice sheets + rolling story-so-far) is directly assertable on the output strings,
  so `tests/test_prompts.py` needs no backend double.
- Demonstrates the pipeline deliberately mixes LLM stages (premise/characters/structure/chapters)
  with deterministic assembly stages. A stage does not have to call the model.
- If a future need arises for LLM-polished prompt phrasing, it can be added as an optional pass;
  the deterministic assembly stays the default.
