"""Exports (roadmap D): .docx and Obsidian/Scrivener .zip, driven through the real app."""

from __future__ import annotations

import io
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


def _generate(c: TestClient) -> str:
    body = {"spec": {"genre": "cozy romance", "tropes": ["x"], "chapters": 3}, "backend": "fake"}
    r = c.post("/api/generate", json=body)
    assert r.status_code == 200
    return str(r.json()["id"])


def test_export_docx(tmp_path: Path) -> None:
    c = _client(tmp_path)
    bid = _generate(c)
    r = c.get(f"/api/bibles/{bid}/export?format=docx")
    assert r.status_code == 200
    assert r.headers["content-type"] == DOCX_MEDIA_TYPE
    assert ".docx" in r.headers["content-disposition"]
    # a .docx is a zip whose members include the main document part
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert "word/document.xml" in zf.namelist()


def test_export_obsidian_zip(tmp_path: Path) -> None:
    c = _client(tmp_path)
    bid = _generate(c)
    r = c.get(f"/api/bibles/{bid}/export?format=obsidian")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
        assert any(n.endswith("00-overview.md") for n in names)
        assert any(n.endswith("-worldbuilding.md") for n in names)
        assert any(n.endswith("-writing-prompts.md") for n in names)
        assert len(names) == 8  # overview + 7 sections


def test_export_bad_format(tmp_path: Path) -> None:
    c = _client(tmp_path)
    bid = _generate(c)
    assert c.get(f"/api/bibles/{bid}/export?format=pdf").status_code == 400


def test_export_missing_bible(tmp_path: Path) -> None:
    c = _client(tmp_path)
    assert c.get("/api/bibles/nope/export?format=docx").status_code == 404
