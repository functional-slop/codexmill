"""Shared-AI access for non-admin users (ADR 0025).

A non-admin generates with the server's AI only when the instance allows it (global toggle) and
their per-user switch is on; admins are exempt. This file also guards the admin-only override
lockdown. The per-user bring-your-own key path (ADR 0027) is covered in ``test_user_byo.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from codexmill.web.app import create_app
from codexmill.web.library import Library
from codexmill.web.store import ConfigStore
from conftest import sign_in


def _root_client(tmp_path: Path) -> TestClient:
    app = create_app(store=ConfigStore(tmp_path / "c.json"), library=Library(tmp_path / "b.db"))
    return sign_in(TestClient(app))  # the setup account is role=root


def _user_client(
    root: TestClient, username: str = "alice", pw: str = "alice-pw-1234"
) -> TestClient:
    root.post("/api/admin/users", json={"username": username, "password": pw})
    u = TestClient(root.app)
    assert u.post("/api/auth/login", json={"username": username, "password": pw}).status_code == 200
    return u


def _uid(root: TestClient, username: str) -> str:
    users = root.get("/api/admin/users").json()
    return str(next(u["id"] for u in users if u["username"] == username))


def _set_server_ai(root: TestClient) -> None:
    """Give the instance an actual server AI. Permission alone is not enough — `can_generate` also
    requires one to EXIST, or the UI would promise an AI that isn't there. Keyless on purpose: the
    default-shareable case is a free/local server AI (a keyless Ollama), which non-admins may use
    without the paid-key opt-in. Tests about a PAID shared key configure that explicitly."""
    assert (
        root.put(
            "/api/admin/llm",
            json={"backend": "fake", "base_url": "http://srv/v1", "model": "m", "api_key": ""},
        ).status_code
        == 200
    )


def test_shared_ai_default_on_user_can_generate(tmp_path: Path) -> None:
    root = _root_client(tmp_path)
    _set_server_ai(root)
    u = _user_client(root)
    assert root.get("/api/admin/shared-ai").json()["allow_shared_ai"] is True  # default
    assert u.get("/api/me").json()["can_generate"] is True
    assert root.get("/api/me").json()["can_generate"] is True


def test_permission_without_a_configured_server_ai_is_not_ready(tmp_path: Path) -> None:
    """Regression: shared-AI defaults are ON, so a user used to be told "AI ready" on an instance
    whose admin had never set up an AI — then generation fell through to the built-in localhost
    default and failed. Permission must not imply readiness."""
    root = _root_client(tmp_path)  # no server AI configured
    u = _user_client(root, "nora", "nora-pw-1234")
    me = u.get("/api/me").json()
    assert me["shared_ai_permitted"] is True  # allowed...
    assert me["has_server_ai"] is False  # ...but there is nothing to use
    assert me["can_generate"] is False
    assert me["ai_source"] == "none"
    r = u.post("/api/generate", json={"spec": {"genre": "cozy romance", "chapters": 3}})
    assert r.status_code == 409  # a clear "no AI set up", not an obscure connection error


def test_global_toggle_off_blocks_non_admin_only(tmp_path: Path) -> None:
    root = _root_client(tmp_path)
    _set_server_ai(root)
    u = _user_client(root)
    assert root.put("/api/admin/shared-ai", json={"allow_shared_ai": False}).status_code == 200
    assert u.get("/api/me").json()["can_generate"] is False  # non-admin locked out
    assert root.get("/api/me").json()["can_generate"] is True  # admin unaffected
    # and the generation endpoint refuses the non-admin
    body = {"spec": {"genre": "cozy romance", "chapters": 3}}
    assert u.post("/api/generate", json=body).status_code == 403


def test_per_user_switch_off_blocks_that_user(tmp_path: Path) -> None:
    root = _root_client(tmp_path)
    u = _user_client(root, "bob", "bob-pw-1234")
    root.patch(f"/api/admin/users/{_uid(root, 'bob')}", json={"use_server_engine": False})
    # global is on, but bob's own switch is off -> he can't generate
    assert u.get("/api/me").json()["can_generate"] is False


def test_shared_ai_toggle_is_admin_only(tmp_path: Path) -> None:
    root = _root_client(tmp_path)
    u = _user_client(root, "carol", "carol-pw-123")
    assert u.get("/api/admin/shared-ai").status_code == 403
    assert u.put("/api/admin/shared-ai", json={"allow_shared_ai": False}).status_code == 403


def test_non_admin_cannot_override_base_url_or_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-admin must NOT be able to redirect generation at an arbitrary endpoint via the
    per-request override fields. Doing so would SSRF the host's network and, by supplying a
    base_url with no key, exfiltrate the shared server key to the attacker's host. The override
    is admin-only; a non-admin's base_url/api_key in the body is ignored."""
    import codexmill.web.app as appmod
    from codexmill.llm import FakeBackend

    root = _root_client(tmp_path)
    assert (
        root.put(
            "/api/admin/llm",
            json={
                "backend": "fake",
                "base_url": "http://server-configured.internal/v1",
                "model": "server-model",
                "api_key": "SERVER-SECRET",
            },
        ).status_code
        == 200
    )
    # this server AI carries a key; opt into sharing it so the test can exercise a non-admin
    # generation and prove the override is ignored (the paid-key guard is tested separately).
    assert (
        root.put(
            "/api/admin/shared-ai",
            json={"allow_shared_ai": True, "allow_shared_paid_key": True},
        ).status_code
        == 200
    )
    u = _user_client(root, "erin", "erin-pw-1234")

    seen: list[tuple[str | None, str | None]] = []

    def _spy(settings: Any) -> FakeBackend:
        seen.append((settings.base_url, settings.api_key))
        return FakeBackend()

    monkeypatch.setattr(appmod, "make_backend", _spy)

    body = {
        "spec": {"genre": "cozy mystery", "chapters": 3},
        "base_url": "http://attacker.invalid/v1",
        "api_key": None,
    }
    # non-admin: the override is ignored -> the server's own URL + key are used, attacker never hit
    assert u.post("/api/generate", json=body).status_code == 200
    used_base, used_key = seen[-1]
    assert used_base == "http://server-configured.internal/v1"
    assert used_key == "SERVER-SECRET"

    # admin: the override IS honored (sample/testing convenience) -> proves the gate is role-based
    seen.clear()
    assert root.post("/api/generate", json=body).status_code == 200
    admin_base, _admin_key = seen[-1]
    assert admin_base == "http://attacker.invalid/v1"
