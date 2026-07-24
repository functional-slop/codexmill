"""Auth gating (ADR 0008/0009 OIDC + ADR 0024 mandatory local accounts), tested offline against the
real ASGI app. We do NOT test a live IdP login (needs real credentials); we test that a fresh
instance is CLOSED, that the local account gates everything, and that OIDC config set via the admin
API turns SSO on at runtime."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codexmill.web.app import create_app
from codexmill.web.library import Library
from codexmill.web.store import ConfigStore
from conftest import TEST_PASSWORD, TEST_USER, sign_in


def _app(tmp_path: Path) -> TestClient:
    """A fresh, un-set-up instance (no account, no OIDC)."""
    return TestClient(
        create_app(store=ConfigStore(tmp_path / "cfg.json"), library=Library(tmp_path / "b.db"))
    )


def _gen_body() -> dict[str, object]:
    return {"spec": {"genre": "cozy romance", "tropes": ["x"], "chapters": 3}, "backend": "fake"}


def test_fresh_instance_is_closed_until_an_admin_exists(tmp_path: Path) -> None:
    # ADR 0024: no open bootstrap. Nothing works until an account is created.
    c = _app(tmp_path)
    me = c.get("/api/me").json()  # /api/me is the one public endpoint (tells the UI what to do)
    assert me["needs_setup"] is True and me["authenticated"] is False
    assert c.post("/api/generate", json=_gen_body()).status_code == 401
    assert c.get("/api/admin/llm").status_code == 401  # admin surface is NOT open
    assert c.get("/api/bibles").status_code == 401


def test_setup_creates_admin_signs_in_and_closes_setup(tmp_path: Path) -> None:
    c = _app(tmp_path)
    r = c.post("/api/auth/setup", json={"username": "boss", "password": "hunter2-long-enough"})
    assert r.status_code == 200 and r.json()["username"] == "boss"
    # now signed in, and the instance is configured
    me = c.get("/api/me").json()
    assert me["needs_setup"] is False and me["authenticated"] is True and me["username"] == "boss"
    assert c.post("/api/generate", json=_gen_body()).status_code == 200
    # setup can never be used again to take over the instance
    assert (
        c.post(
            "/api/auth/setup", json={"username": "x", "password": "another-password"}
        ).status_code
        == 409
    )


def test_login_logout_and_wrong_password(tmp_path: Path) -> None:
    c = sign_in(_app(tmp_path))
    assert c.post("/api/auth/logout").status_code == 200
    assert c.post("/api/generate", json=_gen_body()).status_code == 401  # session gone
    assert (
        c.post("/api/auth/login", json={"username": TEST_USER, "password": "wrong"}).status_code
        == 401
    )
    assert (
        c.post("/api/auth/login", json={"username": "nope", "password": TEST_PASSWORD}).status_code
        == 401
    )
    assert (
        c.post(
            "/api/auth/login", json={"username": TEST_USER, "password": TEST_PASSWORD}
        ).status_code
        == 200
    )
    assert c.post("/api/generate", json=_gen_body()).status_code == 200


def test_login_is_throttled_after_repeated_failures(tmp_path: Path) -> None:
    # A password form with no throttle is an online brute-force. After 5 failures we lock out.
    c = sign_in(_app(tmp_path))
    c.post("/api/auth/logout")
    for _ in range(5):
        assert (
            c.post("/api/auth/login", json={"username": TEST_USER, "password": "no"}).status_code
            == 401
        )
    locked = c.post("/api/auth/login", json={"username": TEST_USER, "password": "no"})
    assert locked.status_code == 429 and locked.headers.get("Retry-After")
    # even the CORRECT password is refused while locked out
    assert (
        c.post(
            "/api/auth/login", json={"username": TEST_USER, "password": TEST_PASSWORD}
        ).status_code
        == 429
    )


def test_unknown_user_burns_a_hash_so_timing_cannot_enumerate(tmp_path: Path) -> None:
    # An unknown username must cost roughly the same as a known one, or response time is an oracle.
    # Exercises the LIVE login path (Users.verify), which dummy-verifies on the not-found branch.
    from codexmill.web.db import make_engine, url_for_path
    from codexmill.web.users import Users

    users = Users(make_engine(url_for_path(tmp_path / "u.db")))
    users.create("real", "a-real-password-123")
    t0 = time.perf_counter()
    assert users.verify("real", "wrong-password") is None
    known = time.perf_counter() - t0
    t0 = time.perf_counter()
    assert users.verify("nobody", "wrong-password") is None
    unknown = time.perf_counter() - t0
    # The unknown-user path must not be trivially faster (it used to return instantly).
    assert unknown > known / 4, f"unknown={unknown:.4f}s known={known:.4f}s — timing oracle"


def test_setup_is_closed_on_an_oidc_configured_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CRITICAL fix (ADR 0024 audit): an OIDC-configured instance with no local account must NOT let
    # an anonymous caller create the admin via /api/auth/setup. Setup is gated on needs_setup().
    monkeypatch.setenv("CODEXMILL_OIDC_ISSUER", "https://idp.example/o/cm")
    monkeypatch.setenv("CODEXMILL_OIDC_CLIENT_ID", "cid")
    monkeypatch.setenv("CODEXMILL_OIDC_CLIENT_SECRET", "secret")
    c = _app(tmp_path)
    assert c.get("/api/me").json()["needs_setup"] is False
    r = c.post("/api/auth/setup", json={"username": "attacker", "password": "takeover-1234"})
    assert r.status_code == 409  # not available; no takeover


def test_setup_requires_token_when_operator_set_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEXMILL_SETUP_TOKEN", "onetime-abc")
    c = _app(tmp_path)
    assert c.get("/api/me").json()["setup_requires_token"] is True
    # without the token: refused
    assert (
        c.post("/api/auth/setup", json={"username": "boss", "password": "hunter2-long"}).status_code
        == 403
    )
    # with the token: created
    r = c.post(
        "/api/auth/setup",
        json={"username": "boss", "password": "hunter2-long"},
        headers={"X-Setup-Token": "onetime-abc"},
    )
    assert r.status_code == 200


def test_logout_revokes_a_captured_cookie(tmp_path: Path) -> None:
    # Session-epoch fix: logout must invalidate an already-issued cookie, not just the current tab.
    c = sign_in(_app(tmp_path))
    stolen = dict(c.cookies)  # attacker captures the signed session cookie
    assert c.post("/api/generate", json=_gen_body()).status_code == 200
    c.post("/api/auth/logout")
    # replay the captured cookie on a fresh client -> rejected (epoch rotated on logout)
    attacker = TestClient(c.app)
    attacker.cookies.update(stolen)
    assert attacker.post("/api/generate", json=_gen_body()).status_code == 401


def test_test_llm_does_not_send_stored_key_to_a_new_url(tmp_path: Path) -> None:
    # API-key exfil fix: "test connection" against a caller-supplied base_url must NOT reuse the
    # saved provider key. With no key in the body and a new base_url, no Authorization is sent.
    c = sign_in(_app(tmp_path))
    c.put(
        "/api/admin/llm",
        json={"backend": "openai", "base_url": "http://real.example/v1", "api_key": "SECRETKEY"},
    )
    # point test at a DIFFERENT url with no api_key -> the stored key must not be used (it just
    # fails to connect; the point is it can't exfiltrate), and the stored key stays put.
    r = c.post("/api/admin/llm/test", json={"base_url": "http://attacker.example"})
    assert r.status_code == 200 and r.json()["ok"] is False  # connection failed, no key leaked
    assert c.get("/api/admin/llm").json()["has_key"] is True  # stored key untouched


def test_delete_is_kind_scoped(tmp_path: Path) -> None:
    # A book must not be deletable via the series route (and vice-versa).
    c = sign_in(_app(tmp_path))
    bid = c.post("/api/generate", json=_gen_body()).json()["id"]
    assert c.delete(f"/api/series/{bid}").status_code == 404  # wrong kind -> not found
    assert c.get(f"/api/bibles/{bid}").status_code == 200  # book still there
    assert c.delete(f"/api/bibles/{bid}").status_code == 204  # correct kind deletes it


def test_openapi_docs_are_disabled(tmp_path: Path) -> None:
    c = sign_in(_app(tmp_path))
    assert c.get("/openapi.json").status_code == 404
    assert c.get("/docs").status_code == 404


def test_short_password_rejected(tmp_path: Path) -> None:
    c = _app(tmp_path)
    assert (
        c.post("/api/auth/setup", json={"username": "boss", "password": "short"}).status_code == 422
    )


def test_config_store_file_is_owner_only(tmp_path: Path) -> None:
    # The store holds the session-signing secret + password hashes; it must never be group/world
    # readable, not even in the write window (the chmod-after-write race the audit found).
    import stat

    from codexmill.web.store import ConfigStore as CS

    cfg = tmp_path / "cfg.json"
    s = CS(cfg)
    s.create_user("boss", "hunter2-long-enough")  # forces a write
    mode = stat.S_IMODE(cfg.stat().st_mode)
    assert mode & 0o077 == 0, f"config file is group/world-accessible: {oct(mode)}"


def test_password_is_not_stored_in_plaintext(tmp_path: Path) -> None:
    import sqlite3

    cfg = tmp_path / "cfg.json"
    db = tmp_path / "b.db"
    c = TestClient(create_app(store=ConfigStore(cfg), library=Library(db)))
    c.post("/api/auth/setup", json={"username": "boss", "password": "hunter2-long-enough"})
    # cleartext lives nowhere — not in the JSON config
    assert "hunter2-long-enough" not in cfg.read_text(encoding="utf-8")
    # the password lives in the users table as an argon2id hash, never cleartext
    hashes = [r[0] for r in sqlite3.connect(db).execute("SELECT password_hash FROM users")]
    assert hashes and all(h.startswith("$argon2") for h in hashes)
    assert all("hunter2-long-enough" not in h for h in hashes)


def test_legacy_json_account_migrates_to_root_with_its_bibles(tmp_path: Path) -> None:
    from codexmill.config import Settings
    from codexmill.llm import make_backend
    from codexmill.pipeline import build
    from codexmill.schemas import Spec
    from codexmill.web.users import Users

    store = ConfigStore(tmp_path / "c.json")
    store.create_user("oldadmin", "legacy-pw-12345")  # a pre-DB JSON account
    lib = Library(tmp_path / "b.db")
    bible = build(
        Spec(genre="cozy romance", chapters=3),
        make_backend(Settings(backend="fake", base_url="", model="", api_key="", temperature=0.0)),
    )
    lib.save("local", bible)  # bible saved before auth existed
    lib.save("oldadmin", bible)  # bible owned by the legacy username

    create_app(store=store, library=lib)  # building the app runs the one-time migration

    users = Users(lib.engine)
    root = users.by_username("oldadmin")
    assert root is not None and root.role == "root"
    assert store.legacy_users() == []  # JSON store emptied
    assert users.verify("oldadmin", "legacy-pw-12345")  # original password still verifies
    assert len(lib.list(root.id)) == 2  # both bibles re-keyed to the new user id
    assert lib.list("local") == [] and lib.list("oldadmin") == []


def test_disabling_local_blocks_password_login(tmp_path: Path) -> None:
    c = sign_in(_app(tmp_path))
    assert c.get("/api/me").json()["auth_methods"] == ["local"]  # default
    # can't go SSO-only without a working OIDC config (lockout guard)
    assert c.put("/api/admin/auth-methods", json={"methods": ["oidc"]}).status_code == 400
    c.put(
        "/api/admin/oidc",
        json={"issuer": "https://idp.example/o/cm", "client_id": "cid", "client_secret": "sec"},
    )
    assert c.put("/api/admin/auth-methods", json={"methods": ["oidc"]}).status_code == 200
    # a valid password is refused once local is off (SSO-only); recovery is the auth_reset CLI
    r = c.post("/api/auth/login", json={"username": TEST_USER, "password": TEST_PASSWORD})
    assert r.status_code == 403
    assert c.get("/api/me").json()["auth_methods"] == ["oidc"]


def test_configuring_oidc_enables_sso_and_anonymous_is_still_locked(tmp_path: Path) -> None:
    admin = sign_in(_app(tmp_path))  # local admin configures OIDC
    r = admin.put(
        "/api/admin/oidc",
        json={
            "issuer": "https://idp.example/application/o/codexmill",
            "client_id": "cid",
            "client_secret": "secret",
            "admin_emails": ["boss@example.com"],
        },
    )
    assert r.status_code == 200 and r.json()["enabled"] is True
    assert admin.get("/api/me").json()["oidc_enabled"] is True
    # The local admin keeps working; a fresh anonymous caller does not.
    assert admin.post("/api/generate", json=_gen_body()).status_code == 200
    anon = TestClient(admin.app)  # same app, no session
    assert anon.post("/api/generate", json=_gen_body()).status_code == 401
    assert anon.get("/api/admin/oidc").status_code == 401


def test_setup_token_is_break_glass_for_admin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEXMILL_SETUP_TOKEN", "break-glass-123")
    c = sign_in(_app(tmp_path))
    anon = TestClient(c.app)
    assert anon.get("/api/admin/oidc").status_code == 401  # locked out
    assert (
        anon.get("/api/admin/oidc", headers={"X-Setup-Token": "break-glass-123"}).status_code == 200
    )


def test_env_oidc_means_no_local_setup_needed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEXMILL_OIDC_ISSUER", "https://idp.example/o/cm")
    monkeypatch.setenv("CODEXMILL_OIDC_CLIENT_ID", "cid")
    monkeypatch.setenv("CODEXMILL_OIDC_CLIENT_SECRET", "secret")
    monkeypatch.setenv("CODEXMILL_SETUP_TOKEN", "tok")
    c = _app(tmp_path)
    me = c.get("/api/me").json()
    assert me["oidc_enabled"] is True and me["needs_setup"] is False  # SSO is the auth source
    r = c.get("/api/admin/oidc", headers={"X-Setup-Token": "tok"})
    assert r.status_code == 200 and r.json()["source"] == "env"
