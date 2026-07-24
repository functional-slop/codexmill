"""Data passed between pipeline stages. Each stage returns a validated model."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Spec(BaseModel):
    """The user's request, loaded from a YAML spec file. Fields are bounded so an API caller can't
    request an absurd amount of work (each chapter is an LLM call) or stuff the prompt."""

    genre: str = Field(min_length=1, max_length=100)
    tropes: list[str] = Field(default_factory=list, max_length=20)
    premise_hint: str | None = Field(default=None, max_length=2000)
    chapters: int = Field(default=24, ge=1, le=60)
    pov: str = Field(default="third-limited", max_length=40)
    maturity: str = Field(default="teen", max_length=40)  # all-ages | teen | mature | explicit
    target_words: int = Field(default=40000, ge=1000, le=500000)
    framework: str = Field(default="auto", max_length=40)


class StorySeed(BaseModel):
    """An on-the-fly invented story concept to prefill the form ("Surprise me")."""

    genre: str
    premise_hint: str
    tropes: list[str]


class Premise(BaseModel):
    logline: str
    genre: str
    tropes: list[str]
    central_conflict: str
    hook: str


class Worldbuilding(BaseModel):
    history: str
    geography: str
    cultures: str
    factions: str
    systems: str  # magic system, technology, or the rules that govern this world


class Character(BaseModel):
    name: str
    role: str  # protagonist | antagonist | supporting
    motivation: str
    flaw: str
    arc: str
    voice: str  # how they talk/think on the page — the "voice sheet"


class CharacterSet(BaseModel):
    characters: list[Character]


class Chapter(BaseModel):
    number: int
    title: str
    beat: str
    summary: str


class Outline(BaseModel):
    chapters: list[Chapter]


class ChapterExpansion(BaseModel):
    """What the model returns for a single chapter. Identity (number/title/beat) comes from the
    outline, not the model, so output stays deterministic and structure-owned."""

    summary: str
    scene_beats: list[str]
    recap: str  # one-line "what materially changed" — fed forward as rolling summary


class ChapterDetail(BaseModel):
    number: int
    title: str
    beat: str
    summary: str
    scene_beats: list[str]


class ChapterBreakdowns(BaseModel):
    chapters: list[ChapterDetail]


class ChapterPrompt(BaseModel):
    number: int
    title: str
    prompt: str  # ready-to-paste instruction for drafting this chapter's prose


class WritingPrompts(BaseModel):
    prompts: list[ChapterPrompt]


class KDPMetadata(BaseModel):
    keywords: list[str]  # up to 7 Amazon backend search keywords/phrases
    categories: list[str]  # up to 3 category paths
    blurb: str  # ~150-word back-cover marketing blurb
    short_description: str  # one-line pitch


class StoryBible(BaseModel):
    """The full assembled bundle rendered to Markdown."""

    spec: Spec
    premise: Premise
    worldbuilding: Worldbuilding
    characters: CharacterSet
    outline: Outline
    breakdowns: ChapterBreakdowns
    writing_prompts: WritingPrompts
    kdp: KDPMetadata


class SeriesSpec(BaseModel):
    """The user's request for a whole series (ADR 0018). Bounded like Spec — a series is
    books × chapters_per_book LLM calls, so both are capped."""

    genre: str = Field(min_length=1, max_length=100)
    tropes: list[str] = Field(default_factory=list, max_length=20)
    series_premise_hint: str | None = Field(default=None, max_length=2000)
    books: int = Field(default=3, ge=1, le=12)
    chapters_per_book: int = Field(default=12, ge=1, le=40)
    pov: str = Field(default="third-limited", max_length=40)
    maturity: str = Field(default="teen", max_length=40)
    framework: str = Field(default="auto", max_length=40)


class BookPlan(BaseModel):
    number: int
    title: str
    arc_role: str  # this book's role in the series arc, e.g. "setup" / "escalation" / "climax"
    premise_hint: str  # what this book is about, advancing the arc from the prior book


class SeriesPlan(BaseModel):
    series_title: str
    series_arc: str  # the overarching multi-book conflict the whole series resolves
    books: list[BookPlan]


class SeriesBible(BaseModel):
    """A multi-book series. Worldbuilding + recurring cast are generated once and shared by every
    book (continuity by construction); each book's StoryBible embeds them so it renders alone."""

    spec: SeriesSpec
    plan: SeriesPlan
    worldbuilding: Worldbuilding
    recurring_characters: CharacterSet
    books: list[StoryBible]
