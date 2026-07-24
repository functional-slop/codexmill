"""User accounts + access control (ADR 0025), backed by the DB.

Repository over the ``users`` table: creation, argon2id verification (a null hash never logs in via
password), roles, activation, per-user permissions, OIDC identity linking, and per-user session
revocation. Identity for OIDC is the immutable ``(iss, sub)`` pair; the app keys ownership on
``User.id`` (a stable UUID)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from codexmill.web.db import make_session_factory, upgrade_to_head
from codexmill.web.models import User, _uuid
from codexmill.web.passwords import dummy_verify, hash_password, needs_rehash, verify_password

ROLES = ("root", "admin", "user")
ADMIN_ROLES = ("root", "admin")


def default_permissions(role: str) -> dict[str, Any]:
    """Permissions derived from a role at creation time. ``use_server_engine`` is the family-hosting
    lever (may this user consume the shared, server-configured model, or must they bring their own
    key); ``quota`` is a per-user generation cap (``None`` inherits the instance default)."""
    admin = role in ADMIN_ROLES
    return {
        "use_server_engine": True,
        "quota": None,
        "manage_users": admin,
        "manage_settings": admin,
    }


class Users:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = make_session_factory(engine)
        upgrade_to_head(engine)

    # ---- reads ---------------------------------------------------------------------
    def count(self) -> int:
        with self._session() as s:
            return int(s.execute(select(func.count()).select_from(User)).scalar_one())

    def has_users(self) -> bool:
        return self.count() > 0

    def by_id(self, user_id: str) -> User | None:
        with self._session() as s:
            return s.execute(select(User).where(User.id == user_id)).scalar_one_or_none()

    def by_username(self, username: str) -> User | None:
        with self._session() as s:
            return s.execute(select(User).where(User.username == username)).scalar_one_or_none()

    def by_email(self, email: str) -> User | None:
        """The single user with this (normalized) email, or None if none or ambiguous. Email is not
        unique, so a plural match must not silently pick one for OIDC linking."""
        with self._session() as s:
            rows = (
                s.execute(select(User).where(User.email == email.strip().lower())).scalars().all()
            )
        return rows[0] if len(rows) == 1 else None

    def by_oidc(self, iss: str, sub: str) -> User | None:
        with self._session() as s:
            return s.execute(
                select(User).where(User.oidc_iss == iss, User.oidc_sub == sub)
            ).scalar_one_or_none()

    def list_all(self) -> list[User]:
        with self._session() as s:
            return list(s.execute(select(User).order_by(User.created_at)).scalars().all())

    # ---- writes --------------------------------------------------------------------
    def create(
        self,
        username: str,
        password: str | None = None,
        role: str = "user",
        email: str | None = None,
        oidc_iss: str | None = None,
        oidc_sub: str | None = None,
    ) -> User:
        user = User(
            id=_uuid(),
            username=username,
            email=email.strip().lower() if email else None,
            password_hash=hash_password(password) if password else None,
            role=role,
            is_active=True,
            oidc_iss=oidc_iss,
            oidc_sub=oidc_sub,
            permissions=default_permissions(role),
        )
        with self._session.begin() as s:
            s.add(user)
        return user

    def create_with_hash(
        self, username: str, password_hash: str | None, role: str = "user"
    ) -> User:
        """Create a user from an already-computed argon2 hash (used to migrate legacy accounts
        without re-hashing, which we can't do without the plaintext)."""
        user = User(
            id=_uuid(),
            username=username,
            password_hash=password_hash or None,
            role=role,
            is_active=True,
            permissions=default_permissions(role),
        )
        with self._session.begin() as s:
            s.add(user)
        return user

    def verify(self, username: str, password: str) -> User | None:
        """Return the user on a correct password, else ``None``. Unknown, disabled, and federated
        (no local password) accounts all pay a full argon2 verify so response timing can't enumerate
        them. A ``None`` hash NEVER logs in via password (keeps OIDC-only accounts passwordless);
        recovery is the ``auth_reset`` CLI, which sets a real temporary password. The hash rehashes
        when its argon2 parameters change."""
        with self._session.begin() as s:
            u = s.execute(select(User).where(User.username == username)).scalar_one_or_none()
            if u is None or not u.is_active or u.password_hash is None:
                dummy_verify(password)
                return None
            if not verify_password(password, u.password_hash):
                return None
            if needs_rehash(u.password_hash):
                u.password_hash = hash_password(password)
            u.last_seen = datetime.now(UTC)
            return u

    def set_password(self, user_id: str, password: str | None) -> bool:
        """Set (or clear) a password and rotate the session epoch, ending sessions. Clearing (None)
        leaves the account without a local password, so it can only sign in via SSO."""
        with self._session.begin() as s:
            u = s.get(User, user_id)
            if u is None:
                return False
            u.password_hash = hash_password(password) if password else None
            u.session_epoch = _uuid()
            return True

    def set_role(self, user_id: str, role: str) -> bool:
        if role not in ROLES:
            return False
        with self._session.begin() as s:
            u = s.get(User, user_id)
            if u is None:
                return False
            u.role = role
            perms = dict(u.permissions)
            perms["manage_users"] = perms["manage_settings"] = role in ADMIN_ROLES
            u.permissions = perms
            return True

    def set_active(self, user_id: str, active: bool) -> bool:
        with self._session.begin() as s:
            u = s.get(User, user_id)
            if u is None:
                return False
            u.is_active = active
            if not active:
                u.session_epoch = _uuid()  # disabling logs the user out
            return True

    def set_permission(self, user_id: str, key: str, value: Any) -> bool:
        with self._session.begin() as s:
            u = s.get(User, user_id)
            if u is None:
                return False
            perms = dict(u.permissions)
            perms[key] = value
            u.permissions = perms
            return True

    # ---- per-user bring-your-own AI key (ADR 0027) ---------------------------------
    # Isolation contract: these act ONLY on the given user_id (always the authenticated caller's own
    # id, never a client-supplied target), the key is sealed at rest, and the raw key is returned to
    # a caller ONLY by ``user_llm_resolved`` (internal, for building generation settings) — never by
    # anything an API handler serialises back to a client.
    def set_user_llm(self, user_id: str, provider: str, model: str, api_key: str) -> bool:
        """Store this user's own cloud-provider key. ``provider`` must be on the allow-list
        (``web.providers``); the ``base_url`` is taken from there, never from the caller, so a user
        can't redirect the server at an arbitrary endpoint. ``api_key`` is sealed. Returns False if
        the user is gone; raises ValueError on an unknown provider or an empty key."""
        from codexmill.web.crypto import seal
        from codexmill.web.providers import get_provider

        prov = get_provider(provider)
        if prov is None:
            raise ValueError(f"unknown provider: {provider}")
        if not api_key.strip():
            raise ValueError("api_key is required")
        with self._session.begin() as s:
            u = s.get(User, user_id)
            if u is None:
                return False
            u.llm = {
                "provider": prov.name,
                "base_url": prov.base_url,
                "model": (model or prov.default_model or "").strip(),
                "api_key": seal(api_key.strip()),
            }
            return True

    def set_server_model(self, user_id: str, model: str) -> bool:
        """Record which of the SERVER's models this user wants. Stored in the same ``llm`` column as
        a bring-your-own key but under a distinct field and with no ``api_key``, so it is never
        mistaken for a BYO config (``user_llm_resolved`` requires a provider + key). Choosing a
        server model means they are not on their own key, so any stored key is dropped."""
        with self._session.begin() as s:
            u = s.get(User, user_id)
            if u is None:
                return False
            u.llm = {"server_model": model} if model else None
            return True

    def server_model(self, user_id: str) -> str:
        """This user's chosen server model, or "" to mean the instance default."""
        with self._session() as s:
            u = s.get(User, user_id)
            cfg = u.llm if (u is not None and isinstance(u.llm, dict)) else None
        return str((cfg or {}).get("server_model") or "")

    def clear_user_llm(self, user_id: str) -> bool:
        with self._session.begin() as s:
            u = s.get(User, user_id)
            if u is None:
                return False
            u.llm = None
            return True

    def user_llm_status(self, user_id: str) -> dict[str, Any]:
        """Non-secret status of this user's BYO key, for the API. Returns ``has_key``/``provider``/
        ``model`` — NEVER the key value."""
        with self._session() as s:
            u = s.get(User, user_id)
            cfg = u.llm if (u is not None and isinstance(u.llm, dict)) else None
        if not cfg or not cfg.get("api_key"):
            return {"has_key": False, "provider": "", "model": ""}
        return {
            "has_key": True,
            "provider": str(cfg.get("provider", "")),
            "model": str(cfg.get("model", "")),
        }

    def user_llm_resolved(self, user_id: str) -> dict[str, str] | None:
        """INTERNAL ONLY — the user's BYO config with the api_key UNSEALED, for building generation
        settings. Do NOT return this from an API handler. ``base_url`` is re-derived from the
        allow-list so a stale/tampered stored value can't redirect the call. Returns None when no
        usable key is present (a sealed value that fails to decrypt reads as absent)."""
        from codexmill.web.crypto import unseal
        from codexmill.web.providers import get_provider

        with self._session() as s:
            u = s.get(User, user_id)
            cfg = u.llm if (u is not None and isinstance(u.llm, dict)) else None
        if not cfg:
            return None
        prov = get_provider(str(cfg.get("provider", "")))
        sealed = str(cfg.get("api_key", ""))
        key = unseal(sealed) if sealed else ""
        if prov is None or not key:
            return None
        return {"base_url": prov.base_url, "model": str(cfg.get("model", "")), "api_key": key}

    def link_oidc(self, user_id: str, iss: str, sub: str) -> bool:
        with self._session.begin() as s:
            u = s.get(User, user_id)
            if u is None:
                return False
            u.oidc_iss, u.oidc_sub = iss, sub
            return True

    def rotate_epoch(self, user_id: str) -> None:
        with self._session.begin() as s:
            u = s.get(User, user_id)
            if u is not None:
                u.session_epoch = _uuid()

    def delete(self, user_id: str) -> bool:
        with self._session.begin() as s:
            u = s.get(User, user_id)
            if u is None:
                return False
            s.delete(u)
            return True
