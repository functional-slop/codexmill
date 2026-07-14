"""Streaming generation (ADR 0011): the /api/generate/stream endpoint emits per-stage progress
frames then a final result, and persists the bible. Driven through the real app, offline."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from codexmill.web.app import create_app
from codexmill.web.library import Library
from codexmill.web.store import ConfigStore
from conftest import sign_in


def _client(tmp_path: Path) -> TestClient:
    return sign_in(
        TestClient(
            create_app(store=ConfigStore(tmp_path / "c.json"), library=Library(tmp_path / "b.db"))
        )
    )


def _frames(text: str) -> list[dict[str, object]]:
    return [
        json.loads(line[len("data:") :].strip())
        for line in text.splitlines()
        if line.startswith("data:")
    ]


def test_stream_emits_stage_progress_then_result(tmp_path: Path) -> None:
    c = _client(tmp_path)
    body = {"spec": {"genre": "cozy romance", "tropes": ["x"], "chapters": 3}, "backend": "fake"}
    r = c.post("/api/generate/stream", json=body)
    assert r.status_code == 200

    frames = _frames(r.text)
    stages = [f["stage"] for f in frames if "stage" in f]
    done = [f for f in frames if f.get("done")]

    assert stages[:3] == ["premise", "worldbuilding", "characters"]
    assert len(stages) == 7  # one progress frame per stage
    assert len(done) == 1
    assert "## Chapter Breakdowns" in str(done[0]["markdown"])

    # The streamed result was persisted to the library.
    assert len(c.get("/api/bibles").json()) == 1
