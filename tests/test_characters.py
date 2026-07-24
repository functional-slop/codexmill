"""Character stage: names are grounded in the world, and an over-used cliché name (Elara/Lyra/…)
triggers exactly one reroll — a deterministic backstop for the models' strong naming bias."""

from __future__ import annotations

from typing import Any

from codexmill.llm import Usage
from codexmill.schemas import Character, CharacterSet, Premise, Spec, Worldbuilding
from codexmill.stages import characters as characters_stage
from codexmill.stages.characters import _overused_names


def _cast(*names: str) -> CharacterSet:
    return CharacterSet(
        characters=[
            Character(name=n, role="protagonist", motivation="m", flaw="f", arc="a", voice="v")
            for n in names
        ]
    )


_PREMISE = Premise(logline="l", genre="g", tropes=["t"], central_conflict="c", hook="h")
_WORLD = Worldbuilding(history="h", geography="g", cultures="c", factions="f", systems="s")
_SPEC = Spec(genre="epic fantasy", chapters=3)


class _ScriptedBackend:
    """Returns a pre-scripted CharacterSet per call, recording each so we can count rerolls."""

    def __init__(self, *responses: CharacterSet) -> None:
        self._responses = list(responses)
        self.calls = 0
        self.usage = Usage()

    def generate(self, system: str, user: str, schema: type[Any], model: str | None = None) -> Any:
        idx = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        return self._responses[idx]


def test_overused_names_detects_default_first_names() -> None:
    assert _overused_names(_cast("Lyra Veldar", "Bram Ashfield")) == ["Lyra Veldar"]
    assert _overused_names(_cast("Kaelen Stonehand", "Vexia Thorne")) == [
        "Kaelen Stonehand",
        "Vexia Thorne",
    ]
    assert _overused_names(_cast("Bram", "Ottoline Reeve", "Saba")) == []  # clean cast, no hits


def test_a_clean_cast_is_not_rerolled() -> None:
    backend = _ScriptedBackend(_cast("Bram Ashfield", "Sena of the Reeds"))
    out = characters_stage.generate(backend, _SPEC, _PREMISE, _WORLD)
    assert backend.calls == 1  # no reroll
    assert [c.name for c in out.characters] == ["Bram Ashfield", "Sena of the Reeds"]


def test_a_cliche_name_triggers_exactly_one_reroll() -> None:
    cliche = _cast("Lyra Veldar", "Kaelen Stonehand")
    fresh = _cast("Bram Ashfield", "Sena of the Reeds")
    backend = _ScriptedBackend(cliche, fresh)
    out = characters_stage.generate(backend, _SPEC, _PREMISE, _WORLD)
    assert backend.calls == 2  # generated once, rerolled once
    assert [c.name for c in out.characters] == ["Bram Ashfield", "Sena of the Reeds"]


def test_reroll_happens_at_most_once_even_if_still_cliche() -> None:
    # If the model repeats a cliché on the reroll too, we accept it rather than loop forever.
    backend = _ScriptedBackend(_cast("Lyra One"), _cast("Elara Two"))
    out = characters_stage.generate(backend, _SPEC, _PREMISE, _WORLD)
    assert backend.calls == 2  # one reroll, then stop
    assert out.characters[0].name == "Elara Two"
