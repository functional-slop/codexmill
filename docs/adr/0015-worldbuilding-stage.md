# 15. Worldbuilding stage

Date: 2026-07-10 · Status: accepted · Roadmap: competitive-parity #1

## Context
The 2026-07-10 benchmark found StoryCraftr (the one open-source rival with viewable output) breaks
out worldbuilding sub-files (history/geography/culture/magic-system) that our bundle lacked. It's
genre-general and a clean win, so we added it (series/continuity is the bigger follow-up; prose
drafting was declined — see ROADMAP).

## Decision
A new pipeline stage `stages/worldbuilding.py` runs after premise and produces a `Worldbuilding`
model with five sections: **history, geography, cultures, factions, systems** (magic / technology
/ governing rules, tailored to genre). It's threaded through `build_iter` (now 7 stages), added to
`StoryBible`, rendered as a "## Worldbuilding" section between KDP metadata and Characters, and it
participates in per-stage model selection like the other LLM stages.

## Consequences
- The bundle now matches the viewable competitor's worldbuilding depth and stays genre-general
  (systems = magic OR tech OR plain rules).
- One more LLM call per generation (7 stages, was 6); progress streaming reflects it.
- `StoryBible` gained a required `worldbuilding` field — external callers constructing one directly
  must provide it (the fake backend + tests were updated).
