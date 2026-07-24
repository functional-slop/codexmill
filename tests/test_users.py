"""User accounts + UAC repository (ADR 0025): argon2 verify with recovery, roles, activation,
OIDC linking, and per-identity session revocation."""

from __future__ import annotations

from pathlib import Path

from codexmill.web.db import make_engine, url_for_path
from codexmill.web.users import Users


def _users(tmp_path: Path) -> Users:
    return Users(make_engine(url_for_path(tmp_path / "u.db")))


def test_create_and_verify(tmp_path: Path) -> None:
    u = _users(tmp_path)
    assert not u.has_users()
    u.create("alice", "pw-12345", role="root")
    assert u.has_users() and u.count() == 1
    assert u.verify("alice", "pw-12345") is not None
    assert u.verify("alice", "wrong") is None
    assert u.verify("ghost", "pw-12345") is None  # unknown user -> None (still pays a dummy verify)


def test_roles_grant_admin_permissions(tmp_path: Path) -> None:
    u = _users(tmp_path)
    bob = u.create("bob", "pw-12345", role="user")
    assert bob.permissions["manage_users"] is False
    assert u.set_role(bob.id, "admin")
    reloaded = u.by_id(bob.id)
    assert reloaded is not None
    assert reloaded.role == "admin"
    assert reloaded.permissions["manage_users"] is True


def test_disabled_user_cannot_log_in(tmp_path: Path) -> None:
    u = _users(tmp_path)
    carol = u.create("carol", "pw-12345")
    assert u.set_active(carol.id, False)
    assert u.verify("carol", "pw-12345") is None


def test_null_hash_denies_all_password_login(tmp_path: Path) -> None:
    u = _users(tmp_path)
    root = u.create("root", "pw-12345", role="root")
    assert u.set_password(root.id, None)  # clear the local password (e.g. now SSO-only)
    assert u.verify("root", "") is None  # a null hash is never blank-loginable
    assert u.verify("root", "pw-12345") is None
    assert u.verify("root", "anything") is None


def test_oidc_link_and_lookup(tmp_path: Path) -> None:
    u = _users(tmp_path)
    dave = u.create("dave", email="d@example.com")
    assert u.by_oidc("https://idp", "sub-1") is None
    assert u.link_oidc(dave.id, "https://idp", "sub-1")
    found = u.by_oidc("https://idp", "sub-1")
    assert found is not None and found.id == dave.id


def test_epoch_rotation_and_delete(tmp_path: Path) -> None:
    u = _users(tmp_path)
    erin = u.create("erin", "pw-12345")
    before = erin.session_epoch
    u.rotate_epoch(erin.id)
    rotated = u.by_id(erin.id)
    assert rotated is not None and rotated.session_epoch != before
    assert u.delete(erin.id)
    assert u.by_id(erin.id) is None
