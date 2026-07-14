"""Regenerate a single stage (roadmap D): the cascade re-runs only the target + its dependents,
verified by spying on backend calls, plus the /api/bibles/{id}/regenerate endpoint end-to-end."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from codexmill.config import Settings
from codexmill.llm import Backend, make_backend
from codexmill.pipeline import build, regenerate
from codexmill.schemas import Spec, StoryBible
from codexmill.web.app import create_app
from codexmill.web.library import Library
from codexmill.web.store import ConfigStore
from conftest import sign_in


class SpyBackend:
    """Wraps a real backend and records the schema name of every generate() call."""

    def __init__(self, inner: Backend) -> None:
        self.inner = inner
        self.calls: list[str] = []
        self.usage = inner.usage  # share the inner backend's tally (ADR 0021)

    def generate(self, system: str, user: str, schema: Any, model: str | None = None) -> Any:
        self.calls.append(schema.__name__)
        return self.inner.generate(system, user, schema, model)


class MarkWorldBackend:
    """Wraps a backend but stamps a unique marker into the regenerated Worldbuilding, so a test can
    prove the new world actually gets re-threaded into the (rebuilt) writing prompts."""

    MARK = "REGEN_WORLD_MARKER_XYZ"

    def __init__(self, inner: Backend) -> None:
        self.inner = inner
        self.usage = inner.usage

    def generate(self, system: str, user: str, schema: Any, model: str | None = None) -> Any:
        out = self.inner.generate(system, user, schema, model)
        if schema.__name__ == "Worldbuilding":
            out = out.model_copy(update={"history": self.MARK})
        return out


def _fake() -> Backend:
    return make_backend(
        Settings(backend="fake", base_url="", model="", api_key="", temperature=0.0)
    )


def _bible() -> StoryBible:
    return build(Spec(genre="cozy romance", chapters=3), _fake())


def test_regenerate_leaf_only_calls_that_stage() -> None:
    spy = SpyBackend(_fake())
    regenerate(spy, _bible(), "KDP metadata")
    assert set(spy.calls) == {"KDPMetadata"}

    spy = SpyBackend(_fake())
    regenerate(spy, _bible(), "worldbuilding")
    assert set(spy.calls) == {"Worldbuilding"}


def test_regenerate_worldbuilding_rethreads_new_world_into_prompts() -> None:
    # Regenerating the world must re-run the writing prompts so they embed the NEW world (the
    # headline of the worldbuilding-in-prompts feature). Prove it with a marker in the new world.
    original = _bible()
    assert all(MarkWorldBackend.MARK not in p.prompt for p in original.writing_prompts.prompts)
    updated = regenerate(MarkWorldBackend(_fake()), original, "worldbuilding")
    assert updated.worldbuilding.history == MarkWorldBackend.MARK
    assert all(MarkWorldBackend.MARK in p.prompt for p in updated.writing_prompts.prompts)


def test_regenerate_characters_cascades_but_not_upstream() -> None:
    spy = SpyBackend(_fake())
    regenerate(spy, _bible(), "characters")
    called = set(spy.calls)
    # characters + structure + chapters re-run (writing prompts is deterministic, no LLM call)
    assert called == {"CharacterSet", "Outline", "ChapterExpansion"}
    # upstream + independent leaves are NOT re-run
    assert "Premise" not in called
    assert "Worldbuilding" not in called
    assert "KDPMetadata" not in called


def test_regenerate_unknown_stage_raises() -> None:
    try:
        regenerate(_fake(), _bible(), "nonsense")
    except ValueError:
        pass
    else:  # pragma: no cover - failure path
        raise AssertionError("expected ValueError for unknown stage")


def _client(tmp_path: Path) -> TestClient:
    return sign_in(
        TestClient(
            create_app(store=ConfigStore(tmp_path / "c.json"), library=Library(tmp_path / "b.db"))
        )
    )


def test_regenerate_endpoint_updates_stored_bible(tmp_path: Path) -> None:
    c = _client(tmp_path)
    body = {"spec": {"genre": "cozy romance", "chapters": 3}, "backend": "fake"}
    bid = c.post("/api/generate", json=body).json()["id"]

    r = c.post(f"/api/bibles/{bid}/regenerate", json={"stage": "characters", "backend": "fake"})
    assert r.status_code == 200
    assert r.json()["id"] == bid  # same id, updated in place
    assert "## Characters" in r.json()["markdown"]
    # still exactly one bible for this owner (updated, not duplicated)
    assert [b["id"] for b in c.get("/api/bibles").json()] == [bid]


def test_regenerate_endpoint_bad_stage_and_missing(tmp_path: Path) -> None:
    c = _client(tmp_path)
    bid = c.post(
        "/api/generate", json={"spec": {"genre": "x", "chapters": 3}, "backend": "fake"}
    ).json()["id"]
    assert c.post(f"/api/bibles/{bid}/regenerate", json={"stage": "bogus"}).status_code == 400
    assert c.post("/api/bibles/nope/regenerate", json={"stage": "characters"}).status_code == 404
