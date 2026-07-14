"""Stage — worldbuilding: history, geography, cultures, factions, and the world's systems (magic,
technology, or governing rules). Genre-general; the piece StoryCraftr shows that we lacked
(ADR 0015)."""

from __future__ import annotations

from codexmill.llm import Backend
from codexmill.schemas import Premise, Spec, Worldbuilding

SYSTEM = "You are a worldbuilder who grounds stories in coherent, evocative settings."


def generate(backend: Backend, spec: Spec, premise: Premise) -> Worldbuilding:
    user = (
        f"Genre: {spec.genre}\n"
        f"Premise: {premise.logline}\n"
        f"Central conflict: {premise.central_conflict}\n\n"
        "Build the world this story lives in, tailored to the genre. Provide: a brief history "
        "(the events that shaped the present); the geography (key places and how they matter); "
        "the cultures/peoples and their values; the factions or powers in tension; and the "
        "systems — the magic system, technology level, or governing rules that define what is "
        "possible here. Keep each grounded and specific to this premise."
    )
    return backend.generate(SYSTEM, user, Worldbuilding)
