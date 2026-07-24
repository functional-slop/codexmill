"""Library persistence (ADR 0010): unit round-trip + owner isolation, and the /api/bibles
endpoints driven through the real app with the offline fake backend."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from codexmill.config import Settings
from codexmill.llm import make_backend
from codexmill.pipeline import build
from codexmill.schemas import Spec, StoryBible
from codexmill.web.app import create_app
from codexmill.web.library import Library
from codexmill.web.store import ConfigStore
from conftest import sign_in


def _bible() -> StoryBible:
    settings = Settings(backend="fake", base_url="", model="", api_key="", temperature=0.0)
    return build(Spec(genre="cozy romance", chapters=3), make_backend(settings))


def test_library_roundtrip_and_owner_isolation(tmp_path: Path) -> None:
    lib = Library(tmp_path / "b.db")
    bid = lib.save("alice", _bible())
    assert [s.id for s in lib.list("alice")] == [bid]
    assert lib.list("bob") == []  # owner isolation
    assert lib.get("alice", bid) is not None
    assert lib.get("bob", bid) is None
    assert lib.delete("bob", bid) is False
    assert lib.delete("alice", bid) is True
    assert lib.list("alice") == []


def _client(tmp_path: Path) -> TestClient:
    return sign_in(
        TestClient(
            create_app(store=ConfigStore(tmp_path / "c.json"), library=Library(tmp_path / "b.db"))
        )
    )


def test_generate_saves_and_library_endpoints(tmp_path: Path) -> None:
    c = _client(tmp_path)
    body = {"spec": {"genre": "cozy romance", "tropes": ["x"], "chapters": 3}, "backend": "fake"}
    gen = c.post("/api/generate", json=body)
    assert gen.status_code == 200
    bid = gen.json()["id"]

    listed = c.get("/api/bibles").json()
    assert [b["id"] for b in listed] == [bid]
    assert listed[0]["genre"] == "cozy romance"

    got = c.get(f"/api/bibles/{bid}")
    assert got.status_code == 200
    assert "## Chapter Breakdowns" in got.json()["markdown"]

    assert c.delete(f"/api/bibles/{bid}").status_code == 204
    assert c.get("/api/bibles").json() == []
    assert c.get(f"/api/bibles/{bid}").status_code == 404
