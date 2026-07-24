"""Admin user management (ADR 0025): list/create/patch/delete/reset-password through the real app,
plus the guardrails (non-admin forbidden, non-root can't touch root, can't remove the last root or
lock yourself out)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from codexmill.web.app import create_app
from codexmill.web.library import Library
from codexmill.web.store import ConfigStore
from conftest import sign_in


def _root_client(tmp_path: Path) -> TestClient:
    app = create_app(store=ConfigStore(tmp_path / "c.json"), library=Library(tmp_path / "b.db"))
    return sign_in(TestClient(app))  # the setup account is role=root


def test_list_and_create_users(tmp_path: Path) -> None:
    c = _root_client(tmp_path)
    users = c.get("/api/admin/users").json()
    assert len(users) == 1 and users[0]["role"] == "root"
    r = c.post("/api/admin/users", json={"username": "alice", "password": "alice-pw-123"})
    assert r.status_code == 201 and r.json()["role"] == "user" and r.json()["is_active"] is True
    assert len(c.get("/api/admin/users").json()) == 2
    dup = c.post("/api/admin/users", json={"username": "alice", "password": "another-pw-1"})
    assert dup.status_code == 409


def test_non_admin_user_is_forbidden(tmp_path: Path) -> None:
    c = _root_client(tmp_path)
    c.post("/api/admin/users", json={"username": "bob", "password": "bob-pw-1234"})
    bob = TestClient(c.app)
    assert (
        bob.post("/api/auth/login", json={"username": "bob", "password": "bob-pw-1234"}).status_code
        == 200
    )
    assert bob.get("/api/admin/users").status_code == 403  # a plain user is not admin


def test_role_permission_password_and_disable(tmp_path: Path) -> None:
    c = _root_client(tmp_path)
    uid = c.post("/api/admin/users", json={"username": "carol", "password": "carol-pw-123"}).json()[
        "id"
    ]
    assert c.patch(f"/api/admin/users/{uid}", json={"role": "admin"}).json()["role"] == "admin"
    perms = c.patch(f"/api/admin/users/{uid}", json={"use_server_engine": False}).json()[
        "permissions"
    ]
    assert perms["use_server_engine"] is False
    assert c.patch(f"/api/admin/users/{uid}", json={"quota": 5}).json()["permissions"]["quota"] == 5
    # reset password -> the new one works
    assert (
        c.post(f"/api/admin/users/{uid}/password", json={"password": "new-carol-99"}).status_code
        == 200
    )
    carol = TestClient(c.app)
    assert (
        carol.post(
            "/api/auth/login", json={"username": "carol", "password": "new-carol-99"}
        ).status_code
        == 200
    )
    # disable -> the account can no longer log in
    assert (
        c.patch(f"/api/admin/users/{uid}", json={"is_active": False}).json()["is_active"] is False
    )
    fresh = TestClient(c.app)
    assert (
        fresh.post(
            "/api/auth/login", json={"username": "carol", "password": "new-carol-99"}
        ).status_code
        == 401
    )


def test_last_root_and_self_are_protected(tmp_path: Path) -> None:
    c = _root_client(tmp_path)
    root_id = next(u["id"] for u in c.get("/api/admin/users").json() if u["role"] == "root")
    assert c.delete(f"/api/admin/users/{root_id}").status_code == 400  # last root / self
    assert c.patch(f"/api/admin/users/{root_id}", json={"is_active": False}).status_code == 400
    assert c.patch(f"/api/admin/users/{root_id}", json={"role": "user"}).status_code == 400


def test_delete_user(tmp_path: Path) -> None:
    c = _root_client(tmp_path)
    uid = c.post("/api/admin/users", json={"username": "dave", "password": "dave-pw-1234"}).json()[
        "id"
    ]
    assert c.delete(f"/api/admin/users/{uid}").status_code == 204
    assert all(u["username"] != "dave" for u in c.get("/api/admin/users").json())


_GEN = {"spec": {"genre": "cozy romance", "tropes": ["x"], "chapters": 3}}


def _set_server_ai(c: TestClient) -> None:
    """Give the instance a real (fake-backend) server AI, as an ADMIN.

    A non-admin can't supply `backend`/`base_url`/`api_key` per request — those overrides are
    admin-only, or any signed-in user could point the server at an arbitrary host. So a test whose
    caller is a non-admin has to configure the server AI properly, or the run falls through to the
    built-in localhost default and quietly depends on a real Ollama being up on the test machine.
    """
    assert (
        c.put(
            "/api/admin/llm",
            # keyless: a free/local server AI, shareable with non-admins by default (a keyed/paid
            # server AI needs the explicit allow_shared_paid_key opt-in, tested in test_user_byo).
            json={"backend": "fake", "base_url": "http://srv/v1", "model": "m", "api_key": ""},
        ).status_code
        == 200
    )


def test_use_server_engine_permission_is_enforced(tmp_path: Path) -> None:
    c = _root_client(tmp_path)
    _set_server_ai(c)
    uid = c.post("/api/admin/users", json={"username": "eve", "password": "eve-pass-1234"}).json()[
        "id"
    ]
    c.patch(f"/api/admin/users/{uid}", json={"use_server_engine": False})
    eve = TestClient(c.app)
    eve.post("/api/auth/login", json={"username": "eve", "password": "eve-pass-1234"})
    # walled off from the shared AI, and she has no key of her own -> can't generate
    assert eve.post("/api/generate", json=_GEN).status_code == 403
    # a self-supplied endpoint does NOT unlock it: those overrides are ignored for a non-admin
    # (bring-your-own goes through /api/me/llm, which is provider-allow-listed — see ADR 0027)
    own = {**_GEN, "base_url": "http://localhost/v1", "api_key": "own-key"}
    assert eve.post("/api/generate", json=own).status_code == 403


def test_per_user_quota_override_is_enforced(tmp_path: Path) -> None:
    c = _root_client(tmp_path)
    _set_server_ai(c)
    uid = c.post(
        "/api/admin/users", json={"username": "frank", "password": "frank-pass-1234"}
    ).json()["id"]
    c.patch(f"/api/admin/users/{uid}", json={"quota": 1})  # personal cap of 1
    frank = TestClient(c.app)
    frank.post("/api/auth/login", json={"username": "frank", "password": "frank-pass-1234"})
    assert frank.post("/api/generate", json=_GEN).status_code == 200  # 1st is fine
    assert frank.post("/api/generate", json=_GEN).status_code == 429  # over the personal cap
