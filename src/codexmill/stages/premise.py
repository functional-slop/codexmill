"""Stage 1 — premise: fuse genre + tropes into a high-concept logline."""

from __future__ import annotations

from codexmill.llm import Backend
from codexmill.schemas import Premise, Spec

SYSTEM = "You are an expert developmental editor and commercial-fiction strategist."


def generate(backend: Backend, spec: Spec) -> Premise:
    tropes = ", ".join(spec.tropes) if spec.tropes else "(choose 2-3 currently popular tropes)"
    seed = f"Seed idea: {spec.premise_hint}\n" if spec.premise_hint else ""
    user = (
        f"Genre: {spec.genre}\n"
        f"Tropes to feature: {tropes}\n"
        f"{seed}"
        "\nProduce a high-concept premise: a one-sentence logline, a marketing hook, and the "
        "central conflict. Keep the listed tropes and echo them back in the tropes field. "
        # Keep the logline about the ROLE and situation, not a named person: characters are named
        # later, grounded in the world, so a name invented here would just clash with the cast (and
        # tends to be a generic AI default).
        "Refer to people by role (e.g. 'a disgraced cartographer'), not by name — naming happens "
        "in the character stage."
    )
    return backend.generate(SYSTEM, user, Premise)
