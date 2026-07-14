"""On-the-fly story-concept generator for the form's "Surprise me" button. Genuinely invented by
the model (not a fixed list), so repeated presses give different concepts."""

from __future__ import annotations

from codexmill.llm import Backend
from codexmill.schemas import StorySeed

SYSTEM = (
    "You are a wildly inventive developmental editor who dreams up fresh, specific, commercial "
    "novel concepts on demand."
)

USER = (
    "Invent ONE original novel concept. Give a genre, a single vivid one-sentence premise (a "
    "specific character in a specific situation — not a generic theme), and 2-3 tropes. Make it "
    "unexpected and distinctive; avoid clichés and avoid the most obvious idea. Surprise me."
)


def generate(backend: Backend) -> StorySeed:
    return backend.generate(SYSTEM, USER, StorySeed)
