"""Public story-structure frameworks as data. The beats are a known method, not invented per
run; the structure stage maps them onto chapters. See docs/adr/0004."""

from __future__ import annotations

THREE_ACT: list[str] = [
    "Act I — Setup: ordinary world, protagonist's want vs. need established.",
    "Act I — Inciting incident: the disruption that starts the story.",
    "Act I — First turning point: protagonist commits to the goal.",
    "Act II — Rising action: escalating obstacles, new allies and costs.",
    "Act II — Midpoint: a reversal or revelation raises the stakes.",
    "Act II — Complications close in: the plan frays, relationships strain.",
    "Act II — Second turning point / all is lost: lowest point.",
    "Act III — Climax: protagonist confronts the core conflict, changed.",
    "Act III — Resolution: new equilibrium reflecting the internal arc.",
]

ROMANCE: list[str] = [
    "Setup: both leads in their flawed status quo, chemistry seeded.",
    "Meet / forced proximity: the relationship engine starts.",
    "No way / adhesion: a reason they must keep interacting despite friction.",
    "Deepening: growing intimacy, guards lowering, first real vulnerability.",
    "Midpoint shift: a moment of connection that changes the stakes.",
    "Doubts creep in: external pressure and internal flaws resurface.",
    "Break up / dark moment: the flaw wins; they pull apart.",
    "Grand gesture: the changed character risks vulnerability.",
    "Happily ever after / for now: earned union reflecting both arcs.",
]


def select(genre: str, framework: str) -> tuple[str, list[str]]:
    """Return (framework_name, beats). `framework` overrides genre when not 'auto'."""
    choice = framework.lower().strip()
    if choice in {"romance", "romancing-the-beat"}:
        return ("romance", ROMANCE)
    if choice in {"three-act", "3-act"}:
        return ("three-act", THREE_ACT)
    if "romance" in genre.lower():
        return ("romance", ROMANCE)
    return ("three-act", THREE_ACT)
