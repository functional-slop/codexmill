"""Drive the real FastAPI app (not a reimplementation) with the offline fake backend. See
docs/VERIFY.md — verifying by exercising the actual endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codexmill.web.app import app, create_app
from codexmill.web.library import Library
from codexmill.web.store import ConfigStore
from conftest import sign_in

# Auth is mandatory (ADR 0024): this shared client carries an authenticated admin session.
client = sign_in(TestClient(app))


def test_health() -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_index_page_served() -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "CodexMill" in r.text
    assert "AGPL-3.0" in r.text  # source/license footer


def test_security_headers_present() -> None:
    r = client.get("/")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_me_reports_source_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEXMILL_SOURCE_URL", "https://example.com/codexmill")
    c = TestClient(
        create_app(store=ConfigStore(tmp_path / "c.json"), library=Library(tmp_path / "b.db"))
    )
    assert c.get("/api/me").json()["source_url"] == "https://example.com/codexmill"


def test_generate_offline_fake() -> None:
    body = {
        "spec": {"genre": "cozy romance", "tropes": ["small-town"], "chapters": 3},
        "backend": "fake",
    }
    r = client.post("/api/generate", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["filename"].endswith(".md")
    for section in ("# Story Bible", "## KDP Metadata", "## Writing Prompts"):
        assert section in data["markdown"]


def test_generate_reports_usage_meter() -> None:
    # ADR 0021: a generation surfaces a per-run token tally (fake backend synthesizes it).
    body = {
        "spec": {"genre": "cozy romance", "tropes": ["small-town"], "chapters": 3},
        "backend": "fake",
    }
    data = client.post("/api/generate", json=body).json()
    usage = data["usage"]
    assert usage["calls"] >= 6  # premise, world, characters, structure, chapters..., KDP
    assert usage["total_tokens"] > 0
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


def test_loading_a_saved_bible_reports_zero_usage() -> None:
    # Opening a stored bible costs nothing, so its meter is empty.
    body = {"spec": {"genre": "cozy romance", "chapters": 3}, "backend": "fake"}
    bid = client.post("/api/generate", json=body).json()["id"]
    usage = client.get(f"/api/bibles/{bid}").json()["usage"]
    assert usage["total_tokens"] == 0 and usage["calls"] == 0


def test_library_records_and_accumulates_tokens() -> None:
    # The saved item carries its token cost, shown in the library; a regenerate adds to it.
    body = {"spec": {"genre": "cozy romance", "chapters": 3}, "backend": "fake"}
    bid = client.post("/api/generate", json=body).json()["id"]

    def tokens_of(target: str) -> int:
        items = client.get("/api/bibles").json()
        return int(next(i["tokens"] for i in items if i["id"] == target))

    first = tokens_of(bid)
    assert first > 0
    r = client.post(
        f"/api/bibles/{bid}/regenerate", json={"stage": "KDP metadata", "backend": "fake"}
    )
    assert r.status_code == 200
    assert tokens_of(bid) > first  # regenerate accumulates onto the item's running total


def test_library_records_generation_time() -> None:
    # Each saved item carries the wall-clock seconds it took to generate, shown in the library;
    # a regenerate adds its time to the running total.
    body = {"spec": {"genre": "cozy romance", "chapters": 3}, "backend": "fake"}
    bid = client.post("/api/generate", json=body).json()["id"]

    def gen_seconds_of(target: str) -> float:
        items = client.get("/api/bibles").json()
        return float(next(i["gen_seconds"] for i in items if i["id"] == target))

    first = gen_seconds_of(bid)
    assert first >= 0.0  # present and non-negative (fake backend is near-instant)
    client.post(f"/api/bibles/{bid}/regenerate", json={"stage": "KDP metadata", "backend": "fake"})
    assert gen_seconds_of(bid) >= first  # regenerate time accumulates onto the running total


def test_surprise_returns_a_seed() -> None:
    r = client.post("/api/surprise", json={"backend": "fake"})
    assert r.status_code == 200
    d = r.json()
    assert d["genre"] and d["premise_hint"]  # a usable invented concept
    assert isinstance(d["tropes"], list)


def test_generate_rejects_invalid_spec() -> None:
    r = client.post("/api/generate", json={"spec": {"tropes": ["x"]}, "backend": "fake"})
    assert r.status_code == 422  # missing required 'genre'


def test_generate_rejects_out_of_bounds_spec() -> None:
    # bounds reject BEFORE any LLM work (a cost/DoS guard): 422, not a hung 200.
    for spec in (
        {"genre": "x", "chapters": 100000},  # absurd chapter count
        {"genre": "x", "chapters": 0},  # below minimum
        {"genre": "", "chapters": 5},  # empty genre
        {"genre": "x", "premise_hint": "z" * 5000},  # prompt stuffing
    ):
        r = client.post("/api/generate", json={"spec": spec, "backend": "fake"})
        assert r.status_code == 422, spec


def test_series_rejects_out_of_bounds_spec() -> None:
    for spec in (
        {"genre": "x", "books": 9999},
        {"genre": "x", "chapters_per_book": 1000},
        {"genre": "x", "books": 0},
    ):
        r = client.post("/api/series", json={"spec": spec, "backend": "fake"})
        assert r.status_code == 422, spec
