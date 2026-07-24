"""Per-user generation quotas (ADR 0022, Milestone F.2), driven through the real app on the
offline fake backend. Auth is mandatory (ADR 0024): the tests sign in, so the quota owner is the
signed-in admin account."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from codexmill.web.app import create_app
from codexmill.web.library import Library
from codexmill.web.store import ConfigStore
from conftest import sign_in

_SPEC = {"genre": "cozy romance", "tropes": ["x"], "chapters": 3}


def _client(tmp_path: Path) -> TestClient:
    return sign_in(
        TestClient(
            create_app(store=ConfigStore(tmp_path / "c.json"), library=Library(tmp_path / "b.db"))
        )
    )


def test_unlimited_by_default(tmp_path: Path) -> None:
    c = _client(tmp_path)
    assert c.get("/api/admin/rate-limit").json()["enabled"] is False
    for _ in range(3):  # no limit configured -> every generation succeeds
        assert c.post("/api/generate", json={"spec": _SPEC, "backend": "fake"}).status_code == 200


def test_quota_blocks_after_limit(tmp_path: Path) -> None:
    c = _client(tmp_path)
    r = c.put("/api/admin/rate-limit", json={"max_generations": 2, "window_hours": 24})
    assert r.status_code == 200 and r.json()["enabled"] is True

    assert c.post("/api/generate", json={"spec": _SPEC, "backend": "fake"}).status_code == 200
    assert c.post("/api/generate", json={"spec": _SPEC, "backend": "fake"}).status_code == 200
    blocked = c.post("/api/generate", json={"spec": _SPEC, "backend": "fake"})
    assert blocked.status_code == 429
    assert "limit of 2 generations" in blocked.json()["detail"]
    assert blocked.headers.get("Retry-After")


def test_quota_counts_streaming_and_series(tmp_path: Path) -> None:
    # The stream and series entry points consume the same per-owner budget.
    c = _client(tmp_path)
    c.put("/api/admin/rate-limit", json={"max_generations": 1, "window_hours": 24})
    assert c.post("/api/generate", json={"spec": _SPEC, "backend": "fake"}).status_code == 200
    # Budget spent: a streaming generation is now refused before the stream opens.
    assert (
        c.post("/api/generate/stream", json={"spec": _SPEC, "backend": "fake"}).status_code == 429
    )
    series_spec = {"genre": "cozy romance", "books": 2, "chapters_per_book": 2}
    assert c.post("/api/series", json={"spec": series_spec, "backend": "fake"}).status_code == 429


def test_surprise_also_consumes_quota(tmp_path: Path) -> None:
    # /api/surprise makes an LLM call, so it must count against the quota (not a free bypass).
    c = _client(tmp_path)
    c.put("/api/admin/rate-limit", json={"max_generations": 1, "window_hours": 24})
    assert c.post("/api/surprise", json={"backend": "fake"}).status_code == 200
    assert c.post("/api/surprise", json={"backend": "fake"}).status_code == 429


def test_removing_limit_restores_unlimited(tmp_path: Path) -> None:
    c = _client(tmp_path)
    c.put("/api/admin/rate-limit", json={"max_generations": 1, "window_hours": 24})
    assert c.post("/api/generate", json={"spec": _SPEC, "backend": "fake"}).status_code == 200
    assert c.post("/api/generate", json={"spec": _SPEC, "backend": "fake"}).status_code == 429
    c.put("/api/admin/rate-limit", json={"max_generations": 0, "window_hours": 24})  # remove
    assert c.get("/api/admin/rate-limit").json()["enabled"] is False
    assert c.post("/api/generate", json={"spec": _SPEC, "backend": "fake"}).status_code == 200


def test_invalid_request_does_not_consume_quota(tmp_path: Path) -> None:
    # A 422 (bad spec) is rejected before the quota gate, so it doesn't burn a slot.
    c = _client(tmp_path)
    c.put("/api/admin/rate-limit", json={"max_generations": 1, "window_hours": 24})
    bad = c.post("/api/generate", json={"spec": {"genre": "", "chapters": 3}, "backend": "fake"})
    assert bad.status_code == 422
    # The one allowed generation is still available.
    assert c.post("/api/generate", json={"spec": _SPEC, "backend": "fake"}).status_code == 200
