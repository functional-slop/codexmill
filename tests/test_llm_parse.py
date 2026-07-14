"""Regression tests for `_parse_payload` — the tolerance layer that lets weaker models' JSON
still validate. Driven by a real failure: gemma4:e4b returned {"chapterExpansion": {...}}."""

from __future__ import annotations

from codexmill.llm import _parse_payload
from codexmill.schemas import ChapterExpansion, Premise


def test_unwraps_single_schema_named_wrapper_key() -> None:
    raw = '{"chapterExpansion": {"summary": "s", "scene_beats": ["a"], "recap": "r"}}'
    obj = ChapterExpansion.model_validate(_parse_payload(raw, ChapterExpansion))
    assert obj.summary == "s"
    assert obj.recap == "r"


def test_passes_through_bare_object() -> None:
    raw = '{"summary": "s", "scene_beats": ["a", "b"], "recap": "r"}'
    obj = ChapterExpansion.model_validate(_parse_payload(raw, ChapterExpansion))
    assert obj.scene_beats == ["a", "b"]


def test_strips_code_fences() -> None:
    raw = '```json\n{"summary": "s", "scene_beats": [], "recap": "r"}\n```'
    obj = ChapterExpansion.model_validate(_parse_payload(raw, ChapterExpansion))
    assert obj.summary == "s"


def test_does_not_unwrap_legit_single_field_overlap() -> None:
    # A schema whose real field is a single key must NOT be treated as a wrapper.
    raw = '{"logline": "L", "genre": "g", "tropes": [], "central_conflict": "c", "hook": "h"}'
    obj = Premise.model_validate(_parse_payload(raw, Premise))
    assert obj.logline == "L"
