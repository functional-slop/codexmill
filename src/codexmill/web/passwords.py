"""Password hashing for local accounts (ADR 0024).

**argon2id** via `argon2-cffi`, the OWASP first-choice KDF. A per-password salt and the library's
current parameters are embedded in the stored hash string, so parameters can be raised later and
existing users transparently upgrade on their next successful login (`needs_rehash`). Verification
is constant-time and never leaks whether the failure was a bad user or a bad password."""

from __future__ import annotations

from typing import Any


def _hasher() -> Any:
    from argon2 import PasswordHasher

    return PasswordHasher()  # argon2id with the library's current recommended parameters


def hash_password(password: str) -> str:
    """Return an argon2id hash string (contains the algorithm, parameters, and salt)."""
    return str(_hasher().hash(password))


def verify_password(password: str, stored: str) -> bool:
    """True if `password` matches `stored`. Any malformed/failed verification is just False."""
    from argon2.exceptions import Argon2Error, InvalidHashError

    if not password or not stored:
        return False
    try:
        return bool(_hasher().verify(stored, password))
    except (Argon2Error, InvalidHashError, TypeError, ValueError):
        return False


def needs_rehash(stored: str) -> bool:
    """True when the stored hash used weaker parameters than we use now (upgrade on next login)."""
    try:
        return bool(_hasher().check_needs_rehash(stored))
    except Exception:
        return False


_DUMMY_HASH: str | None = None


def dummy_verify(password: str) -> None:
    """Burn the same work a real verify would.

    Without this, an unknown username returns instantly while a known one costs a full argon2
    verify (~100ms) — the difference is measurable, which hands an attacker a username oracle.
    Call this on the user-not-found path so both branches take the same time."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("codexmill-timing-equalizer")
    verify_password(password, _DUMMY_HASH)
