"""Render a StoryBible to a Markdown bundle.

`bible_sections` is the single source of section content; both the flat Markdown bundle
(`render_bible`) and the per-section Obsidian/Scrivener export (`codexmill.export`) build on it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from codexmill.schemas import SeriesBible, StoryBible


def slugify(text: str, limit: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:limit] or "story-bible"


@dataclass(frozen=True)
class Section:
    """One top-level section of the bible. `body` is Markdown without the leading `## title`."""

    slug: str
    title: str
    body: str


def _premise(b: StoryBible) -> str:
    p = b.premise
    return "\n".join(
        [
            f"**Logline.** {p.logline}",
            "",
            f"**Hook.** {p.hook}",
            "",
            f"**Central conflict.** {p.central_conflict}",
        ]
    )


def _kdp(b: StoryBible) -> str:
    k = b.kdp
    lines = [
        f"**Short description.** {k.short_description}",
        "",
        "**Blurb.**",
        "",
        k.blurb,
        "",
        "**Keywords:** " + ", ".join(k.keywords),
        "",
        "**Categories:**",
    ]
    lines.extend(f"- {c}" for c in k.categories)
    return "\n".join(lines)


def _worldbuilding(b: StoryBible) -> str:
    w = b.worldbuilding
    lines: list[str] = []
    for title, text in (
        ("History", w.history),
        ("Geography", w.geography),
        ("Cultures & peoples", w.cultures),
        ("Factions & powers", w.factions),
        ("Systems & rules", w.systems),
    ):
        lines += [f"### {title}", "", text, ""]
    return "\n".join(lines).rstrip()


def _characters(b: StoryBible) -> str:
    lines: list[str] = []
    for c in b.characters.characters:
        lines += [
            f"### {c.name} — {c.role}",
            f"- **Motivation:** {c.motivation}",
            f"- **Flaw:** {c.flaw}",
            f"- **Arc:** {c.arc}",
            f"- **Voice:** {c.voice}",
            "",
        ]
    return "\n".join(lines).rstrip()


def _structure(b: StoryBible) -> str:
    lines: list[str] = []
    for ch in b.outline.chapters:
        lines += [f"### Chapter {ch.number}: {ch.title}", f"*Beat:* {ch.beat}", "", ch.summary, ""]
    return "\n".join(lines).rstrip()


def _breakdowns(b: StoryBible) -> str:
    lines: list[str] = []
    for cd in b.breakdowns.chapters:
        lines += [f"### Chapter {cd.number}: {cd.title}", f"*Beat:* {cd.beat}", "", cd.summary, ""]
        if cd.scene_beats:
            lines.append("**Scene beats:**")
            lines.extend(f"- {b_}" for b_ in cd.scene_beats)
            lines.append("")
    return "\n".join(lines).rstrip()


def _writing_prompts(b: StoryBible) -> str:
    lines = ["Paste each block into any LLM to draft that chapter's prose.", ""]
    for wp in b.writing_prompts.prompts:
        lines += [f"### Chapter {wp.number}: {wp.title}", "", "```text", wp.prompt, "```", ""]
    return "\n".join(lines).rstrip()


def bible_sections(bible: StoryBible) -> list[Section]:
    return [
        Section("premise", "Premise", _premise(bible)),
        Section("kdp-metadata", "KDP Metadata", _kdp(bible)),
        Section("worldbuilding", "Worldbuilding", _worldbuilding(bible)),
        Section("characters", "Characters", _characters(bible)),
        Section("structure", "Structure", _structure(bible)),
        Section("chapter-breakdowns", "Chapter Breakdowns", _breakdowns(bible)),
        Section("writing-prompts", "Writing Prompts", _writing_prompts(bible)),
    ]


def render_bible(bible: StoryBible) -> str:
    lines: list[str] = [
        "# Story Bible",
        "",
        f"*Genre:* {bible.spec.genre}  ",
        f"*Tropes:* {', '.join(bible.premise.tropes)}",
    ]
    for s in bible_sections(bible):
        lines += ["", f"## {s.title}", "", s.body]
    return "\n".join(lines).rstrip() + "\n"


# Sections shown once at the series level and skipped inside each book (they are shared).
_SHARED_SLUGS = {"worldbuilding", "characters"}


def render_series(series: SeriesBible) -> str:
    """Render a whole series: the arc + book lineup + shared world + recurring cast once, then each
    book's book-specific sections. Reuses ``bible_sections`` (ADR 0018)."""
    plan = series.plan
    lines: list[str] = [
        f"# {plan.series_title}",
        "",
        f"*Genre:* {series.spec.genre}  ",
        f"*Books:* {len(series.books)}",
        "",
        "## Series Arc",
        "",
        plan.series_arc,
        "",
        "## Books in this Series",
        "",
    ]
    for bp in plan.books:
        lines.append(f"{bp.number}. **{bp.title}** — *{bp.arc_role}.* {bp.premise_hint}")
    lines.append("")

    # Shared world + cast, rendered once from the first book (every book embeds the same objects).
    shared = {s.slug: s for s in bible_sections(series.books[0])} if series.books else {}
    if "worldbuilding" in shared:
        lines += ["## Shared Worldbuilding", "", shared["worldbuilding"].body, ""]
    if "characters" in shared:
        lines += ["## Recurring Cast", "", shared["characters"].body, ""]

    for bp, book in zip(plan.books, series.books, strict=False):
        lines += ["---", "", f"# Book {bp.number}: {bp.title}", ""]
        for s in bible_sections(book):
            if s.slug in _SHARED_SLUGS:
                continue
            lines += ["", f"## {s.title}", "", s.body]

    return "\n".join(lines).rstrip() + "\n"
