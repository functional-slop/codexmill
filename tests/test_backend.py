"""Unit-level checks on the offline fake backend and schema validation."""

from __future__ import annotations

import pytest

from codexmill.config import Settings
from codexmill.llm import BackendError, FakeBackend, OpenAIBackend
from codexmill.schemas import CharacterSet, Outline, Premise


def test_fake_backend_returns_valid_premise() -> None:
    premise = FakeBackend().generate("sys", "user", Premise)
    assert premise.logline
    assert premise.tropes


def test_fake_backend_returns_cast_and_outline() -> None:
    cast = FakeBackend().generate("sys", "user", CharacterSet)
    outline = FakeBackend().generate("sys", "user", Outline)
    assert len(cast.characters) >= 2
    assert [c.number for c in outline.chapters] == [1, 2, 3]


def test_fake_outline_honors_requested_chapter_count() -> None:
    outline = FakeBackend().generate("sys", "Target: 24 chapters, POV third", Outline)
    assert len(outline.chapters) == 24
    assert [c.number for c in outline.chapters[:3]] == [1, 2, 3]
    assert outline.chapters[-1].number == 24


def test_fake_backend_unknown_schema_raises() -> None:
    class Unknown(Premise):
        pass

    with pytest.raises(BackendError):
        FakeBackend().generate("sys", "user", Unknown)


def test_openai_backend_unreachable_endpoint_is_clean_error() -> None:
    # A wrong/unreachable base URL must surface a friendly BackendError, not hang or leak a raw
    # connection traceback. Port 1 refuses immediately — no network egress, no API call, no cost.
    be = OpenAIBackend(
        Settings(
            backend="openai",
            base_url="http://127.0.0.1:1/v1",
            model="nope",
            api_key="x",
            temperature=0.0,
            timeout=2.0,
        )
    )
    with pytest.raises(BackendError) as exc:
        be.generate("sys", "user", Premise)
    assert "AI engine" in str(exc.value)  # user-facing wording, not a raw stack
