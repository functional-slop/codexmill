"""Stage 2 — characters: cast with motivation, flaw, arc, and a voice sheet each."""

from __future__ import annotations

import re

from codexmill.llm import Backend
from codexmill.schemas import CharacterSet, Premise, Spec, Worldbuilding

SYSTEM = "You are a character-focused novelist who writes distinct, consistent voices."

# First-name roots the common models over-produce for fiction regardless of setting ("Elara",
# "Lyra", "Kaelen", "Thorne"…). Verified against a local model: grounding + a "don't use these"
# instruction still didn't reliably stop them, so we DETECT them in the returned cast and reroll
# the stage once. Matched as a lowercased prefix of any token in a character's name, so "Lyra
# Veldar"/"Kaelen Stonehand"/"Vexia Thorne" all trip it. A rare false positive just costs one extra
# LLM call and yields a fresh name — cheap, and only when a cliché actually appears.
_OVERUSED_ROOTS = (
    "elara",
    "elowen",
    "lyra",
    "kael",
    "thorn",
    "seraphin",
    "aiden",
    "kyra",
    "lyric",
    "isolde",
    "aeliana",
    "sylvara",
)


def _overused_names(cast: CharacterSet) -> list[str]:
    """Character names whose tokens match an over-used root (empty when the cast is clean)."""
    hits: list[str] = []
    for character in cast.characters:
        tokens = re.findall(r"[A-Za-z]+", character.name)
        if any(tok.lower().startswith(root) for tok in tokens for root in _OVERUSED_ROOTS):
            hits.append(character.name)
    return hits


def generate(backend: Backend, spec: Spec, premise: Premise, world: Worldbuilding) -> CharacterSet:
    user = (
        f"Premise: {premise.logline}\n"
        f"Central conflict: {premise.central_conflict}\n"
        f"Genre: {spec.genre}\n"
        # Feed the already-established world so names + culture fit the setting, instead of the
        # model inventing a cast untethered from it (where its generic defaults creep in).
        f"Setting — cultures & peoples: {world.cultures}\n"
        f"Setting — geography: {world.geography}\n\n"
        "Design the core cast (protagonist, antagonist or romantic counterpart, and 1-2 "
        "supporting). For each give: name, role, motivation, a defining flaw, their arc, and a "
        "voice sheet describing exactly how they talk and think on the page (vocabulary, rhythm, "
        "verbal tics) so they never blur together.\n\n"
        "Naming matters: DERIVE each name from the naming conventions implied by the cultures, "
        "era, and geography above — the sounds, roots, and traditions of THIS world's peoples — so "
        "a name reads as belonging to a specific culture here, not generic fantasy. Give the cast "
        "varied origins where the setting has distinct peoples. (Naming the world's cultures "
        "first, then a person from within one, avoids the bland defaults that come from a vacuum.)"
    )
    cast = backend.generate(SYSTEM, user, CharacterSet)

    # Deterministic backstop: the models lean hard on a handful of default names even when told to
    # ground them, so if any slipped through, reject them by name and regenerate the cast ONCE. A
    # single reroll bounds the cost; if the model repeats itself we accept it rather than loop.
    stale = _overused_names(cast)
    if stale:
        reroll = (
            f"{user}\n\nThese names are over-used clichés and are REJECTED: "
            f"{', '.join(sorted(set(stale)))}. Regenerate the ENTIRE cast with completely "
            "different names — different opening sounds and roots — each derived from the cultures "
            "above. Do not reuse any rejected name or a near-variant of it."
        )
        cast = backend.generate(SYSTEM, reroll, CharacterSet)
    return cast
