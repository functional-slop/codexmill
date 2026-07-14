"""Series web surface (ADR 0018): /api/series CRUD + stream, and kind-isolation from /api/bibles.
Driven through the real app with the offline fake backend."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from codexmill.export import DOCX_MEDIA_TYPE
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


def _series_body() -> dict[str, object]:
    return {
        "spec": {"genre": "cozy mystery", "books": 2, "chapters_per_book": 2},
        "backend": "fake",
    }


def test_series_generate_and_crud(tmp_path: Path) -> None:
    c = _client(tmp_path)
    gen = c.post("/api/series", json=_series_body())
    assert gen.status_code == 200
    sid = gen.json()["id"]
    assert "-series.md" in gen.json()["filename"]
    md = gen.json()["markdown"]
    assert "## Series Arc" in md and "# Book 1:" in md and "# Book 2:" in md

    listed = c.get("/api/series").json()
    assert [s["id"] for s in listed] == [sid]

    got = c.get(f"/api/series/{sid}")
    assert got.status_code == 200
    assert "## Recurring Cast" in got.json()["markdown"]

    assert c.delete(f"/api/series/{sid}").status_code == 204
    assert c.get("/api/series").json() == []
    assert c.get(f"/api/series/{sid}").status_code == 404


def test_series_and_books_are_kind_isolated(tmp_path: Path) -> None:
    c = _client(tmp_path)
    book = c.post(
        "/api/generate", json={"spec": {"genre": "x", "chapters": 2}, "backend": "fake"}
    ).json()["id"]
    series = c.post("/api/series", json=_series_body()).json()["id"]

    # a book never shows up under /api/series and a series never under /api/bibles
    assert [s["id"] for s in c.get("/api/series").json()] == [series]
    assert [b["id"] for b in c.get("/api/bibles").json()] == [book]
    # cross-kind fetch is a 404
    assert c.get(f"/api/bibles/{series}").status_code == 404
    assert c.get(f"/api/series/{book}").status_code == 404


def test_series_export_docx_and_zip(tmp_path: Path) -> None:
    c = _client(tmp_path)
    sid = c.post("/api/series", json=_series_body()).json()["id"]

    docx = c.get(f"/api/series/{sid}/export?format=docx")
    assert docx.status_code == 200
    assert docx.headers["content-type"] == DOCX_MEDIA_TYPE
    with zipfile.ZipFile(io.BytesIO(docx.content)) as zf:
        assert "word/document.xml" in zf.namelist()

    z = c.get(f"/api/series/{sid}/export?format=obsidian")
    assert z.status_code == 200
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        names = zf.namelist()
        assert any(n.endswith("00-series-overview.md") for n in names)
        assert any("book-01-" in n for n in names) and any("book-02-" in n for n in names)
        # shared world/cast live once at the series root, not inside each book folder
        assert any(n.endswith("/01-worldbuilding.md") for n in names)

    assert c.get(f"/api/series/{sid}/export?format=pdf").status_code == 400


def test_series_regenerate_book_in_place(tmp_path: Path) -> None:
    c = _client(tmp_path)
    sid = c.post("/api/series", json=_series_body()).json()["id"]

    r = c.post(f"/api/series/{sid}/regenerate", json={"book": 2, "backend": "fake"})
    assert r.status_code == 200
    assert r.json()["id"] == sid  # same series, updated in place
    assert "# Book 2:" in r.json()["markdown"]
    assert [s["id"] for s in c.get("/api/series").json()] == [sid]  # not duplicated

    # out-of-range book is a 400; unknown series is a 404
    assert c.post(f"/api/series/{sid}/regenerate", json={"book": 9}).status_code == 400
    assert c.post("/api/series/nope/regenerate", json={"book": 1}).status_code == 404


def test_series_stream_emits_progress_then_done(tmp_path: Path) -> None:
    c = _client(tmp_path)
    with c.stream("POST", "/api/series/stream", json=_series_body()) as resp:
        assert resp.status_code == 200
        events = []
        for line in resp.iter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:].strip()))
    assert any("stage" in e for e in events)  # progress ticks
    done = [e for e in events if e.get("done")]
    assert len(done) == 1
    assert "# " in done[0]["markdown"]
    # the streamed series was persisted
    assert [s["id"] for s in c.get("/api/series").json()] == [done[0]["id"]]
