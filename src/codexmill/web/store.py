"""CodexMill's first bit of persistent state: a small JSON config store for runtime-editable
settings (OIDC config, the session secret, admin allowlist). Written atomically, chmod 600.
Path is `CODEXMILL_CONFIG_DIR/codexmill.json` (default: an OS state dir; the Docker image sets
`CODEXMILL_CONFIG_DIR=/data`). See docs/adr/0009."""

from __future__ import annotations

import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any

from codexmill.web.crypto import seal, unseal, write_private


def default_config_path() -> Path:
    base = os.environ.get("CODEXMILL_CONFIG_DIR")
    if base:
        return Path(base) / "codexmill.json"
    # Outside the repo so a dev checkout stays clean; a real deploy sets CODEXMILL_CONFIG_DIR.
    return Path.home() / ".local" / "state" / "codexmill" / "codexmill.json"


class ConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or default_config_path()
        self._lock = threading.Lock()
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            loaded: Any = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                return loaded
        return {}

    def _save(self) -> None:
        # codexmill.json holds the session-signing secret (plaintext) +
        # sealed API/OIDC blobs. Write it 0600-from-creation so it is never briefly world-readable.
        write_private(self._path, json.dumps(self._data, indent=2))

    def session_secret(self) -> str:
        """Stable secret for signing session cookies; generated once and persisted."""
        with self._lock:
            s = self._data.get("session_secret")
            if not isinstance(s, str) or not s:
                s = secrets.token_hex(32)
                self._data["session_secret"] = s
                self._save()
            return s

    def get_oidc(self) -> dict[str, Any]:
        oidc = self._data.get("oidc")
        if not isinstance(oidc, dict):
            return {}
        out = dict(oidc)
        if out.get("client_secret"):
            out["client_secret"] = unseal(str(out["client_secret"]))
        return out

    def set_oidc(self, oidc: dict[str, Any]) -> None:
        out = dict(oidc)
        if out.get("client_secret"):
            out["client_secret"] = seal(str(out["client_secret"]))
        with self._lock:
            self._data["oidc"] = out
            self._save()

    def get_llm(self) -> dict[str, Any]:
        llm = self._data.get("llm")
        if not isinstance(llm, dict):
            return {}
        out = dict(llm)
        if out.get("api_key"):
            out["api_key"] = unseal(str(out["api_key"]))
        return out

    def set_llm(self, llm: dict[str, Any]) -> None:
        out = dict(llm)
        if out.get("api_key"):
            out["api_key"] = seal(str(out["api_key"]))
        with self._lock:
            self._data["llm"] = out
            self._save()

    # ---- legacy JSON accounts (pre-DB) — migration path ONLY -----------------------
    # Live accounts + session revocation now live on the DB `users` table (web/users.py,
    # models.User.session_epoch). These three exist solely to migrate a pre-DB instance's JSON
    # accounts into that table on startup (app._migrate_legacy_users).
    def _users(self) -> dict[str, Any]:
        users = self._data.get("users")
        return dict(users) if isinstance(users, dict) else {}

    def legacy_users(self) -> list[tuple[str, str]]:
        """(username, argon2 hash) pairs from the pre-DB JSON store, for one-time migration."""
        out: list[tuple[str, str]] = []
        for name, rec in self._users().items():
            if isinstance(rec, dict):
                out.append((str(name), str(rec.get("password", ""))))
        return out

    def clear_legacy_users(self) -> None:
        """Drop the JSON user store once accounts are migrated into the DB (one source of truth)."""
        with self._lock:
            self._data.pop("users", None)
            self._save()

    def create_user(self, username: str, password: str) -> None:
        """Write a pre-DB-style JSON account. Only used to seed a legacy account for the migration
        test; live accounts are created in the DB (web/users.py)."""
        from codexmill.web.passwords import hash_password

        with self._lock:
            users = self._users()
            users[username] = {"password": hash_password(password)}
            self._data["users"] = users
            self._save()

    def get_rate_limit(self) -> dict[str, Any]:
        rl = self._data.get("rate_limit")
        return dict(rl) if isinstance(rl, dict) else {}

    def set_rate_limit(self, rate_limit: dict[str, Any]) -> None:
        with self._lock:
            if rate_limit:
                self._data["rate_limit"] = rate_limit
            else:
                self._data.pop("rate_limit", None)
            self._save()

    def get_auth_methods(self) -> list[str]:
        """Which sign-in methods are active: ``local`` (username+password) and/or ``oidc``. Default
        is local-only. Removing ``local`` disables password login (SSO-only); recovery is the
        ``auth_reset`` CLI, which restores it."""
        v = self._data.get("auth_methods")
        if isinstance(v, list) and v:
            methods = [str(m) for m in v if str(m) in ("local", "oidc")]
            if methods:
                return methods
        return ["local"]

    def set_auth_methods(self, methods: list[str]) -> None:
        clean = [m for m in methods if m in ("local", "oidc")]
        with self._lock:
            self._data["auth_methods"] = clean or ["local"]
            self._save()

    def allow_shared_ai(self) -> bool:
        """Instance toggle: may non-admin users generate with the server's own AI (the admin's model
        + key)? When off, non-admins can't generate at all (no bring-your-own path yet). Per-user
        access is refined in each user's permissions, but only matters when this is on. Default True
        (a personal/family self-host wants signed-in users to just work)."""
        v = self._data.get("allow_shared_ai")
        return True if v is None else bool(v)

    def set_allow_shared_ai(self, allow: bool) -> None:
        with self._lock:
            self._data["allow_shared_ai"] = bool(allow)
            self._save()

    def allow_shared_paid_key(self) -> bool:
        """Second, stricter gate: may non-admins use the shared server AI when it carries an API KEY
        (i.e. a paid cloud endpoint, where every generation spends the operator's quota)? Default
        **False** — sharing a keyless local model is free and fine, but silently spending the
        operator's cloud key on other users must be an explicit, deliberate opt-in. This is the
        guardrail against the exact leak where a server AI pointed at the operator's Gemini key was
        billed by testers. Keyless server AIs (local Ollama) ignore this flag."""
        return bool(self._data.get("allow_shared_paid_key"))

    def set_allow_shared_paid_key(self, allow: bool) -> None:
        with self._lock:
            self._data["allow_shared_paid_key"] = bool(allow)
            self._save()

    def admin_emails(self) -> list[str]:
        v = self._data.get("admin_emails")
        return [str(e) for e in v] if isinstance(v, list) else []

    def set_admin_emails(self, emails: list[str]) -> None:
        with self._lock:
            self._data["admin_emails"] = emails
            self._save()
