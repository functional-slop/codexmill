"""Secrets-at-rest for the config store (ADR 0012, hardened in ADR 0024).

Sealed values are ALWAYS Fernet-encrypted. The key resolves as: `CODEXMILL_SECRET_KEY` env, else a
`secret.key` file (chmod 600) in `CODEXMILL_CONFIG_DIR` that is **auto-generated on first use**.
So a default deploy encrypts the stored API key / OIDC secret with no operator action: encryption
is not opt-in, and forgetting an env var can no longer leave credentials in plaintext.

Losing the key file means sealed values can no longer be decrypted (re-enter the key in Settings);
that is the correct failure mode for encryption-at-rest.

The `enc:v1:` prefix marks a sealed value so unseal is idempotent.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import os
import secrets
from pathlib import Path
from typing import Any

PREFIX = "enc:v1:"


def write_private(path: Path, text: str) -> None:
    """Atomically write ``text`` to ``path`` so it is NEVER world/group-readable, not even for the
    instant between create and chmod. The file is created 0600 from the first byte via ``os.open``
    with ``O_EXCL`` on a fresh temp name, then renamed into place; the parent dir is tightened to
    0700. This is the fix for the chmod-after-write race that would otherwise briefly expose the
    session-signing secret / encryption key (ADR 0024 audit)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.parent.chmod(0o700)  # dir traversable only by the owner
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with contextlib.suppress(FileNotFoundError):
        tmp.unlink()
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    os.replace(tmp, path)


def _config_dir() -> Path:
    base = os.environ.get("CODEXMILL_CONFIG_DIR")
    if base:
        return Path(base)
    return Path.home() / ".local" / "state" / "codexmill"


def _secret() -> str:
    """The raw secret material: env if set, else a persisted auto-generated key file."""
    env = os.environ.get("CODEXMILL_SECRET_KEY")
    if env:
        return env
    path = _config_dir() / "secret.key"
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    # First use: generate and persist a key so encryption is on by default.
    key = secrets.token_hex(32)
    write_private(path, key)  # 0600 from creation — never a world-readable window
    return key


def _fernet() -> Any:
    from cryptography.fernet import Fernet

    key = base64.urlsafe_b64encode(hashlib.sha256(_secret().encode()).digest())
    return Fernet(key)


def seal(value: str) -> str:
    if not value or value.startswith(PREFIX):
        return value
    return PREFIX + str(_fernet().encrypt(value.encode()).decode())


def unseal(value: str) -> str:
    if not value or not value.startswith(PREFIX):
        return value
    from cryptography.fernet import InvalidToken

    try:
        return str(_fernet().decrypt(value[len(PREFIX) :].encode()).decode())
    except (InvalidToken, ValueError):
        # Wrong/lost key: treat the secret as UNSET rather than returning the sealed blob. Returning
        # the blob would send `enc:v1:...` to the LLM endpoint as a Bearer token. Empty = "no key
        # configured", so the app prompts the user to re-enter the secret in Settings.
        return ""
