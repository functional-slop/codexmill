"""Per-user bring-your-own cloud key (ADR 0027).

Covers the two things that must be right: (1) strict isolation — a user's key is never returned to
anyone (not even its owner) and one user can never reach another's; (2) resolution precedence — the
caller's own key wins over the shared server AI, and clearing it falls back.
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
    return sign_in(TestClient(app))  # setup account is role=root


def _user_client(root: TestClient, username: str, pw: str) -> TestClient:
    root.post("/api/admin/users", json={"username": username, "password": pw})
    u = TestClient(root.app)
    assert u.post("/api/auth/login", json={"username": username, "password": pw}).status_code == 200
    return u


def _set_shared_ai(root: TestClient, key: str) -> None:
    assert (
        root.put(
            "/api/admin/llm",
            json={
                "backend": "fake",
                "base_url": "http://server-shared.internal/v1",
                "model": "server-model",
                "api_key": key,
            },
        ).status_code
        == 200
    )
    # This server AI carries a key (a paid cloud AI in production). Sharing a PAID key with
    # non-admins is off by default (the guard against billing testers to the operator's key), so
    # tests that intend non-admins to USE the shared AI must opt in explicitly, like an operator.
    assert (
        root.put(
            "/api/admin/shared-ai",
            json={"allow_shared_ai": True, "allow_shared_paid_key": True},
        ).status_code
        == 200
    )


def test_providers_allowlist_shape(tmp_path: Path) -> None:
    root = _root_client(tmp_path)
    u = _user_client(root, "pat", "pat-pw-12345")
    provs = {p["name"]: p for p in u.get("/api/providers").json()}
    assert "openai" in provs and provs["openai"]["base_url"] == "https://api.openai.com/v1"
    # the allow-list is cloud-only: no bare "ollama"/"custom" that would let a user pass a base_url
    assert "ollama" not in provs and "custom" not in provs
    # unauthenticated cannot enumerate
    assert TestClient(root.app).get("/api/providers").status_code == 401


def test_set_status_never_returns_key(tmp_path: Path) -> None:
    root = _root_client(tmp_path)
    u = _user_client(root, "quinn", "quinn-pw-1234")
    secret = "sk-MY-PRIVATE-KEY-quinn"
    r = u.put("/api/me/llm", json={"provider": "openai", "model": "gpt-4o-mini", "api_key": secret})
    assert r.status_code == 200
    assert secret not in r.text  # the write response must not echo the key
    status = u.get("/api/me/llm")
    assert status.status_code == 200
    body = status.json()
    assert body == {"has_key": True, "provider": "openai", "model": "gpt-4o-mini"}
    assert secret not in status.text  # the status must never carry the key
    # and /api/me must not leak it either
    assert secret not in u.get("/api/me").text


def test_bad_provider_rejected(tmp_path: Path) -> None:
    root = _root_client(tmp_path)
    u = _user_client(root, "rae", "rae-pw-123456")
    # an off-list provider is refused (no arbitrary endpoint)
    assert u.put("/api/me/llm", json={"provider": "evilcorp", "api_key": "x"}).status_code == 400
    # there is no base_url field to smuggle a URL through; a bare "custom" is not allow-listed
    assert u.put("/api/me/llm", json={"provider": "custom", "api_key": "x"}).status_code == 400
    # empty key rejected
    assert u.put("/api/me/llm", json={"provider": "openai", "api_key": ""}).status_code == 400
    assert u.get("/api/me/llm").json()["has_key"] is False  # nothing stored


def test_key_isolation_across_users(tmp_path: Path) -> None:
    root = _root_client(tmp_path)
    a = _user_client(root, "alice", "alice-pw-1234")
    b = _user_client(root, "bob", "bob-pw-123456")
    a_secret = "sk-ALICE-SECRET-0001"
    assert a.put("/api/me/llm", json={"provider": "openai", "api_key": a_secret}).status_code == 200
    # bob sees only his own (empty) status, never alice's key — there is no target-id parameter that
    # could address another user's key, and nothing in any response carries the value
    assert b.get("/api/me/llm").json() == {"has_key": False, "provider": "", "model": ""}
    assert a_secret not in b.get("/api/me/llm").text
    assert a_secret not in b.get("/api/me").text
    # alice still has hers
    assert a.get("/api/me/llm").json()["has_key"] is True


def test_own_key_wins_then_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import codexmill.web.app as appmod
    from codexmill.llm import FakeBackend

    root = _root_client(tmp_path)
    _set_shared_ai(root, "SERVER-SECRET")
    u = _user_client(root, "sam", "sam-pw-123456")

    seen: list[tuple[str | None, str | None]] = []

    def _spy(settings: Any) -> FakeBackend:
        seen.append((settings.base_url, settings.api_key))
        return FakeBackend()

    monkeypatch.setattr(appmod, "make_backend", _spy)

    # with a personal key: generation uses the provider's fixed base_url + the personal key,
    # never the shared server secret
    assert (
        u.put(
            "/api/me/llm", json={"provider": "openai", "model": "gpt-4o-mini", "api_key": "SAM-KEY"}
        ).status_code
        == 200
    )
    assert u.get("/api/me").json()["ai_source"] == "own"
    body = {"spec": {"genre": "space opera", "chapters": 3}}
    assert u.post("/api/generate", json=body).status_code == 200
    used_base, used_key = seen[-1]
    assert used_base == "https://api.openai.com/v1"
    assert used_key == "SAM-KEY"

    # clear the personal key -> falls back to the shared server AI
    assert u.delete("/api/me/llm").status_code in (200, 204)
    assert u.get("/api/me/llm").json()["has_key"] is False
    assert u.get("/api/me").json()["ai_source"] == "server"
    seen.clear()
    assert u.post("/api/generate", json=body).status_code == 200
    used_base2, used_key2 = seen[-1]
    assert used_base2 == "http://server-shared.internal/v1"
    assert used_key2 == "SERVER-SECRET"


def test_new_user_must_choose_an_ai_then_is_not_asked_again(tmp_path: Path) -> None:
    """A new user is not silently dropped onto the server's AI: `ai_onboarded` is False until they
    pick one, and the UI uses that to show the one-time picker. Either choice settles it."""
    root = _root_client(tmp_path)
    _set_shared_ai(root, "SERVER-SECRET")
    a = _user_client(root, "nina", "nina-pw-12345")
    me = a.get("/api/me").json()
    assert me["ai_onboarded"] is False  # never asked yet -> UI shows the picker
    assert me["can_generate"] is True  # the server's AI IS usable, they just haven't chosen
    assert me["server_ai_model"] == "server-model"  # model name shown so the choice is informed

    assert a.post("/api/me/use-server-ai").status_code == 200
    me = a.get("/api/me").json()
    assert me["ai_onboarded"] is True and me["ai_source"] == "server"

    # the other path: saving a key also counts as choosing
    b = _user_client(root, "omar", "omar-pw-12345")
    assert b.get("/api/me").json()["ai_onboarded"] is False
    assert (
        b.put("/api/me/llm", json={"provider": "openai", "api_key": "OMAR-KEY"}).status_code == 200
    )
    me_b = b.get("/api/me").json()
    assert me_b["ai_onboarded"] is True and me_b["ai_source"] == "own"


def test_generation_records_the_model_and_usage_is_split_by_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A token count is only meaningful next to the model that spent it, so the model is stored on
    each bible, surfaced in the library, and /api/me/usage breaks the spend down by model."""
    import codexmill.web.app as appmod
    from codexmill.llm import FakeBackend

    monkeypatch.setattr(appmod, "make_backend", lambda s: FakeBackend())
    root = _root_client(tmp_path)
    _set_shared_ai(root, "SERVER-SECRET")  # model "server-model"
    u = _user_client(root, "vic", "vic-pw-123456")
    body = {"spec": {"genre": "thriller", "chapters": 3}}

    # on the server's model
    r1 = u.post("/api/generate", json=body)
    assert r1.status_code == 200 and r1.json()["model"] == "server-model"

    # switch to a personal key -> a different model gets recorded
    assert (
        u.put(
            "/api/me/llm", json={"provider": "openai", "model": "gpt-4o-mini", "api_key": "VIC"}
        ).status_code
        == 200
    )
    r2 = u.post("/api/generate", json=body)
    assert r2.status_code == 200 and r2.json()["model"] == "gpt-4o-mini"

    # the library carries it per item, and reopening a saved bible still reports it
    models = {b["model"] for b in u.get("/api/bibles").json()}
    assert models == {"server-model", "gpt-4o-mini"}
    assert u.get(f"/api/bibles/{r1.json()['id']}").json()["model"] == "server-model"

    # usage is split by model, and is the caller's own only
    usage = u.get("/api/me/usage").json()
    assert {r["model"] for r in usage["by_model"]} == {"server-model", "gpt-4o-mini"}
    assert usage["items"] == 2
    assert root.get("/api/me/usage").json()["items"] == 0  # admin generated nothing


def test_user_can_pick_among_the_servers_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With access to the server's AI (e.g. a local Ollama) a user may choose among ITS models,
    not just the admin's default, but only from the curated list and never seeing the server URL."""
    import codexmill.web.app as appmod
    from codexmill.llm import FakeBackend

    root = _root_client(tmp_path)
    _set_shared_ai(root, "SERVER-SECRET")  # default model "server-model"
    u = _user_client(root, "wes", "wes-pw-123456")

    import codexmill.web.model_filter as mf

    offered = mf.filter_models(
        ["gemma4:e4b", "nomic-embed-text:latest", "hf.co/x/LightOnOCR-2-1B-GGUF:Q8_0", "big:70b"]
    )
    assert "nomic-embed-text:latest" not in offered and "big:70b" in offered

    # pinning a model the server doesn't offer is refused
    assert u.post("/api/me/use-server-ai", json={"model": "nope:1b"}).status_code == 400
    # the admin's default is always pinnable
    assert u.post("/api/me/use-server-ai", json={"model": "server-model"}).status_code == 200
    me = u.get("/api/me").json()
    assert me["ai_source"] == "server" and me["server_ai_model"] == "server-model"
    assert me["has_own_key"] is False  # a server-model pick is NOT a bring-your-own key

    # and the pinned model is what generation actually uses
    seen: list[str | None] = []

    def _spy(settings: Any) -> FakeBackend:
        seen.append(settings.model)
        return FakeBackend()

    monkeypatch.setattr(appmod, "make_backend", _spy)
    assert (
        u.post("/api/generate", json={"spec": {"genre": "noir", "chapters": 3}}).status_code == 200
    )
    assert seen[-1] == "server-model"

    # the picker endpoint never leaks the server's base_url or key
    body = u.get("/api/me/server-models")
    assert body.status_code == 200
    assert "SERVER-SECRET" not in body.text and "server-shared.internal" not in body.text


def test_server_ai_model_hidden_from_users_without_access(tmp_path: Path) -> None:
    """The model name is shown only to someone allowed to use it, and the server's base_url/key are
    never exposed to anyone."""
    root = _root_client(tmp_path)
    _set_shared_ai(root, "SERVER-SECRET")
    assert root.put("/api/admin/shared-ai", json={"allow_shared_ai": False}).status_code == 200
    u = _user_client(root, "pia", "pia-pw-123456")
    body = u.get("/api/me")
    assert body.json()["shared_ai_permitted"] is False
    assert body.json()["server_ai_model"] == ""
    assert "SERVER-SECRET" not in body.text and "server-shared.internal" not in body.text
    assert u.post("/api/me/use-server-ai").status_code == 403  # can't opt into what they can't use


def test_own_key_generates_even_when_shared_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A personal key lets a user generate even when the instance shares no AI and their
    use_server_engine switch is off — the two are independent capabilities."""
    import codexmill.web.app as appmod
    from codexmill.llm import FakeBackend

    root = _root_client(tmp_path)
    assert root.put("/api/admin/shared-ai", json={"allow_shared_ai": False}).status_code == 200
    u = _user_client(root, "uma", "uma-pw-123456")
    noir = {"spec": {"genre": "noir", "chapters": 3}}
    assert u.get("/api/me").json()["can_generate"] is False  # no key, no shared -> blocked
    assert u.post("/api/generate", json=noir).status_code == 403

    monkeypatch.setattr(appmod, "make_backend", lambda s: FakeBackend())
    assert u.put("/api/me/llm", json={"provider": "groq", "api_key": "UMA-KEY"}).status_code == 200
    me = u.get("/api/me").json()
    assert me["can_generate"] is True and me["ai_source"] == "own"
    assert u.post("/api/generate", json=noir).status_code == 200


def test_byo_test_actually_generates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The 'Test' button must exercise real generation with the chosen model, not just list models,
    so a key that can list models but can't generate with them (wrong tier / not enabled / quota)
    fails the test instead of passing it. The endpoint uses the provider's fixed base_url and the
    submitted key."""
    import codexmill.web.app as appmod
    from codexmill.llm import BackendError

    root = _root_client(tmp_path)
    u = _user_client(root, "wren", "wren-pw-12345")

    seen: dict[str, str | None] = {}

    class _OkBackend:
        def generate(self, system: str, user: str, schema: Any, model: str | None = None) -> Any:
            return schema(ok="ok")

    def ok_backend(settings: Any) -> _OkBackend:
        seen["model"] = settings.model
        seen["base_url"] = settings.base_url
        seen["key"] = settings.api_key
        return _OkBackend()

    monkeypatch.setattr(appmod, "make_backend", ok_backend)
    r = u.post(
        "/api/me/llm/test", json={"provider": "gemini", "api_key": "K", "model": "gemini-2.0-flash"}
    )
    assert r.json() == {"ok": True, "model": "gemini-2.0-flash"}
    # server-fixed endpoint + the submitted key + the chosen model actually reached the backend
    assert seen["base_url"] == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert seen["model"] == "gemini-2.0-flash" and seen["key"] == "K"

    # a key that can't generate with the model surfaces the real error rather than a false pass
    class _FailBackend:
        def generate(self, *a: Any, **k: Any) -> Any:
            raise BackendError("model gemini-2.5-flash is not accessible for your key")

    monkeypatch.setattr(appmod, "make_backend", lambda s: _FailBackend())
    r2 = u.post(
        "/api/me/llm/test", json={"provider": "gemini", "api_key": "K", "model": "gemini-2.5-flash"}
    )
    body = r2.json()
    assert (
        body["ok"] is False
        and "not accessible" in body["error"]
        and body["model"] == "gemini-2.5-flash"
    )


def test_byo_model_dropdown_uses_only_the_callers_submitted_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bring-your-own model dropdown is populated from the models the caller's OWN submitted key
    can list, against the provider's server-fixed endpoint. It must never probe with a stored key
    (the caller's or another user's) and never accept a client base_url. With no key it returns the
    curated fallback so the dropdown is never empty. This is the no-cross-user-bleed guarantee for
    the new /api/me/llm/models endpoint."""
    import httpx

    root = _root_client(tmp_path)
    a = _user_client(root, "iso-a", "iso-a-pw-1234")
    b = _user_client(root, "iso-b", "iso-b-pw-1234")
    # user a stores a key; it must NEVER be used to answer user b's model-list call
    assert (
        a.put("/api/me/llm", json={"provider": "openai", "api_key": "sk-A-STORED"}).status_code
        == 200
    )

    seen: dict[str, Any] = {}

    def fake_get(url: str, headers: Any = None, timeout: Any = None) -> Any:
        seen["url"] = url
        seen["auth"] = (headers or {}).get("Authorization")

        class _Resp:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> Any:
                return {
                    "data": [
                        {"id": "gpt-4o"},
                        {"id": "text-embedding-3-small"},  # must be filtered out (not generative)
                        {"id": "gpt-4o-mini"},
                    ]
                }

        return _Resp()

    monkeypatch.setattr(httpx, "get", fake_get)

    # no key -> curated fallback, and NO network probe happens at all
    r0 = b.post("/api/me/llm/models", json={"provider": "gemini", "api_key": ""}).json()
    assert r0["live"] is False and "gemini-2.0-flash" in r0["models"]
    assert "url" not in seen  # never probed a provider without the user's own key

    # b lists with b's OWN key -> probes the provider's fixed endpoint with b's key, cuts embeddings
    r = b.post("/api/me/llm/models", json={"provider": "openai", "api_key": "sk-B-OWN"}).json()
    assert r["live"] is True
    assert "gpt-4o" in r["models"] and "text-embedding-3-small" not in r["models"]
    assert seen["url"].startswith("https://api.openai.com/v1")  # server-fixed, not client-supplied
    assert seen["auth"] == "Bearer sk-B-OWN"  # b's submitted key, NEVER a's stored "sk-A-STORED"
    assert "sk-A-STORED" not in str(seen)

    # an off-list provider is refused (no arbitrary endpoint reachable through this route)
    assert (
        b.post("/api/me/llm/models", json={"provider": "evil", "api_key": "x"}).status_code == 400
    )


def test_non_admin_cannot_spend_a_paid_server_key_without_optin(tmp_path: Path) -> None:
    """The exact leak this guards: a server AI pointed at the operator's PAID cloud key must NOT be
    usable by a non-admin unless the operator has explicitly opted into sharing a paid key. Default
    off. (A keyless local server AI is free and stays shareable — covered elsewhere.)"""
    root = _root_client(tmp_path)
    # a KEYED server AI, sharing ON, but the paid-key opt-in left at its default (off)
    assert (
        root.put(
            "/api/admin/llm",
            json={
                "backend": "fake",
                "base_url": "http://server-shared.internal/v1",
                "model": "server-model",
                "api_key": "OPERATOR-PAID-KEY",
            },
        ).status_code
        == 200
    )
    assert root.put("/api/admin/shared-ai", json={"allow_shared_ai": True}).status_code == 200
    u = _user_client(root, "cass", "cass-pw-12345")

    me = u.get("/api/me").json()
    assert me["shared_ai_permitted"] is False  # blocked: paid server key, no opt-in
    assert me["can_generate"] is False
    assert me["ai_source"] == "none"
    # generation is refused server-side, not just hidden in the UI
    gen = u.post("/api/generate", json={"spec": {"genre": "noir", "chapters": 3}})
    assert gen.status_code == 403
    # ...and the server never hands the key out via the server-models route either
    assert u.get("/api/me/server-models").status_code == 403

    # the operator deliberately opts in -> now the non-admin may use it
    assert (
        root.put(
            "/api/admin/shared-ai",
            json={"allow_shared_ai": True, "allow_shared_paid_key": True},
        ).status_code
        == 200
    )
    me2 = u.get("/api/me").json()
    assert me2["shared_ai_permitted"] is True and me2["can_generate"] is True


def test_providers_expose_a_model_dropdown(tmp_path: Path) -> None:
    root = _root_client(tmp_path)
    u = _user_client(root, "xander", "xander-pw-1234")
    provs = {p["name"]: p for p in u.get("/api/providers").json()}
    assert "gemini-2.0-flash" in provs["gemini"]["models"]  # dropdown options present
    assert provs["gemini"]["default_model"] == "gemini-2.0-flash"
