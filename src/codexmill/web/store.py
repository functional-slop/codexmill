"""CodexMill's first bit of persistent state: a small JSON config store for runtime-editable
settings (OIDC config, the session secret, admin allowlist). Written atomically, chmod 600.
Path is `CODEXMILL_CONFIG_DIR/codexmill.json` (default `./data`). See docs/adr/0009."""

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
        # codexmill.json holds the session-signing secret (plaintext) + argon2 password hashes +
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

    # ---- local accounts (ADR 0024) -------------------------------------------------
    def _users(self) -> dict[str, Any]:
        users = self._data.get("users")
        return dict(users) if isinstance(users, dict) else {}

    def has_users(self) -> bool:
        """True once a local account exists — i.e. the instance is past first-run setup."""
        return bool(self._users())

    def usernames(self) -> list[str]:
        return sorted(self._users().keys())

    def create_user(self, username: str, password: str) -> None:
        """Create a local account. Caller enforces the 'only during first-run setup' rule."""
        from codexmill.web.passwords import hash_password

        with self._lock:
            users = self._users()
            users[username] = {"password": hash_password(password)}
            self._data["users"] = users
            self._save()

    def verify_user(self, username: str, password: str) -> bool:
        """Verify a local account. Transparently upgrades the hash if parameters have changed.
        An unknown username still pays a full argon2 verify (`dummy_verify`) so response time can't
        be used to enumerate valid usernames."""
        from codexmill.web.passwords import dummy_verify, needs_rehash, verify_password

        user = self._users().get(username)
        if not isinstance(user, dict):
            dummy_verify(password)  # equalize timing with the found-user path
            return False
        stored = str(user.get("password", ""))
        if not verify_password(password, stored):
            return False
        if needs_rehash(stored):
            self.create_user(username, password)  # re-hash with current parameters
        return True

    def set_password(self, username: str, password: str) -> bool:
        if username not in self._users():
            return False
        self.create_user(username, password)
        self.rotate_session_epoch(username)  # a password change kills existing sessions
        return True

    # ---- session revocation (ADR 0024 audit) ---------------------------------------
    # Sessions are stateless signed cookies, so "logout" can't reach a stolen copy on its own. We
    # stamp each session with a per-identity epoch token and check it on every request; rotating the
    # epoch (on logout or password change) invalidates every cookie ever issued for that identity.
    def _epochs(self) -> dict[str, Any]:
        e = self._data.get("session_epochs")
        return dict(e) if isinstance(e, dict) else {}

    def current_session_epoch(self, identity: str) -> str:
        """The identity's current epoch, or "" if none (read-only; cheap for per-request checks)."""
        v = self._epochs().get(identity)
        return str(v) if isinstance(v, str) else ""

    def ensure_session_epoch(self, identity: str) -> str:
        """Return the identity's epoch, minting + persisting one on first login."""
        with self._lock:
            epochs = self._epochs()
            cur = epochs.get(identity)
            if not isinstance(cur, str) or not cur:
                cur = secrets.token_hex(16)
                epochs[identity] = cur
                self._data["session_epochs"] = epochs
                self._save()
            return str(cur)

    def rotate_session_epoch(self, identity: str) -> None:
        """Invalidate every existing session for this identity (logout / password change)."""
        with self._lock:
            epochs = self._epochs()
            epochs[identity] = secrets.token_hex(16)
            self._data["session_epochs"] = epochs
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

    def admin_emails(self) -> list[str]:
        v = self._data.get("admin_emails")
        return [str(e) for e in v] if isinstance(v, list) else []

    def set_admin_emails(self, emails: list[str]) -> None:
        with self._lock:
            self._data["admin_emails"] = emails
            self._save()
