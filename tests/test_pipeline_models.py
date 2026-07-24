"""Per-stage model selection (ADR 0005, roadmap C pt.2): the model mapped to a stage reaches that
stage's backend call, and unmapped stages get the default (None). Records the model per call."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from codexmill.llm import FakeBackend
from codexmill.pipeline import build
from codexmill.schemas import Spec

T = TypeVar("T", bound=BaseModel)


class RecordingFake:
    """Conforms to the Backend protocol; records (schema, model) per call, data from FakeBackend."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self._fake = FakeBackend()
        self.usage = self._fake.usage  # satisfy the Backend protocol (ADR 0021)

    def generate(self, system: str, user: str, schema: type[T], model: str | None = None) -> T:
        self.calls.append((schema.__name__, model))
        return self._fake.generate(system, user, schema)


def test_per_stage_model_reaches_the_right_stage() -> None:
    backend = RecordingFake()
    build(Spec(genre="cozy romance", chapters=3), backend, {"premise": "M1", "chapters": "M2"})

    by_schema: dict[str, list[str | None]] = {}
    for name, model in backend.calls:
        by_schema.setdefault(name, []).append(model)

    assert by_schema["Premise"] == ["M1"]  # premise override applied
    assert by_schema["CharacterSet"] == [None]  # no override -> default
    assert set(by_schema["ChapterExpansion"]) == {"M2"}  # every chapter call uses M2


def test_no_stage_models_leaves_everything_default() -> None:
    backend = RecordingFake()
    build(Spec(genre="cozy romance", chapters=3), backend)
    assert all(model is None for _, model in backend.calls)
