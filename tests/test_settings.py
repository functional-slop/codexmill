"""Saved server-side LLM settings + secrets-at-rest (ADR 0012). Store round-trip + encryption, and
the resolution precedence (request > stored > env) through the real app with the fake backend."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
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


def _gen(genre: str = "cozy romance") -> dict[str, object]:
    return {"spec": {"genre": genre, "tropes": ["x"], "chapters": 3}}


def test_store_llm_roundtrip_and_encryption_at_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEXMILL_SECRET_KEY", "unit-test-secret")
    path = tmp_path / "c.json"
    store = ConfigStore(path)
    store.set_llm({"backend": "openai", "model": "gpt", "api_key": "sk-super-secret"})

    on_disk = json.loads(path.read_text())
    assert on_disk["llm"]["api_key"].startswith("enc:v1:")  # not plaintext
    assert "sk-super-secret" not in path.read_text()
    assert store.get_llm()["api_key"] == "sk-super-secret"  # transparently decrypted


def test_stored_llm_used_when_request_omits_backend(tmp_path: Path) -> None:
    c = _client(tmp_path)
    assert c.put("/api/admin/llm", json={"backend": "fake"}).status_code == 200
    assert c.get("/api/me").json()["has_server_ai"] is True
    # No backend in the request -> falls back to the stored "fake" default.
    r = c.post("/api/generate", json=_gen())
    assert r.status_code == 200
    assert len(c.get("/api/bibles").json()) == 1


def test_request_override_beats_stored(tmp_path: Path) -> None:
    c = _client(tmp_path)
    # Store an unreachable openai default...
    c.put("/api/admin/llm", json={"backend": "openai", "base_url": "http://127.0.0.1:1/v1"})
    # ...but the request explicitly asks for fake, which must win.
    body = {**_gen(), "backend": "fake"}
    assert c.post("/api/generate", json=body).status_code == 200


def test_llm_status_never_returns_key(tmp_path: Path) -> None:
    c = _client(tmp_path)
    c.put("/api/admin/llm", json={"backend": "openai", "api_key": "sk-secret"})
    status = c.get("/api/admin/llm").json()
    assert status["has_key"] is True
    assert "api_key" not in status
