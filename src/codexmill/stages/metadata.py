"""Stage 6 — KDP metadata: Amazon publishing metadata from the premise.

Neither StoryCraftr nor AIStoryWriter ships publishing metadata; this closes the self-publish
loop and is an explicit ADR-0005 differentiator."""

from __future__ import annotations

from codexmill.llm import Backend
from codexmill.schemas import KDPMetadata, Premise, Spec

SYSTEM = "You are an Amazon KDP publishing strategist who writes high-converting book metadata."


def generate(backend: Backend, spec: Spec, premise: Premise) -> KDPMetadata:
    tropes = ", ".join(premise.tropes) if premise.tropes else spec.genre
    user = (
        f"Genre: {spec.genre}\n"
        f"Tropes: {tropes}\n"
        f"Logline: {premise.logline}\n"
        f"Hook: {premise.hook}\n\n"
        "Produce Amazon KDP metadata: up to 7 backend keyword phrases readers actually search "
        "for; up to 3 specific Amazon category paths; a roughly 150-word back-cover blurb; and a "
        "one-line short description."
    )
    return backend.generate(SYSTEM, user, KDPMetadata)
