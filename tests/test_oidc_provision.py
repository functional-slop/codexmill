"""OIDC provisioning + linking (ADR 0025): the security-critical identity mapping, unit-tested
without a live IdP. Token validation is Authlib's job; this maps a trusted (iss, sub) to a local
account per policy."""

from __future__ import annotations

from pathlib import Path

from codexmill.web.db import make_engine, url_for_path
from codexmill.web.oidc_provision import OIDCProvisioning, provision
from codexmill.web.users import Users

ISS = "https://idp.example"


def _users(tmp_path: Path) -> Users:
    return Users(make_engine(url_for_path(tmp_path / "u.db")))


def test_existing_subject_returns_same_user(tmp_path: Path) -> None:
    u = _users(tmp_path)
    created = u.create("alice", None, oidc_iss=ISS, oidc_sub="sub-1")
    got = provision(u, ISS, "sub-1", {"sub": "sub-1"}, OIDCProvisioning())
    assert got is not None and got.id == created.id


def test_unmatched_is_denied_without_autoregister(tmp_path: Path) -> None:
    u = _users(tmp_path)
    claims = {"sub": "new", "email": "x@e.com", "email_verified": True}
    assert provision(u, ISS, "new", claims, OIDCProvisioning()) is None


def test_autoregister_creates_user(tmp_path: Path) -> None:
    u = _users(tmp_path)
    claims = {"sub": "new", "preferred_username": "bob", "email": "b@e.com", "email_verified": True}
    got = provision(u, ISS, "new", claims, OIDCProvisioning(auto_register=True))
    assert got is not None
    assert got.role == "user" and got.oidc_sub == "new" and got.email == "b@e.com"


def test_email_match_links_only_when_verified(tmp_path: Path) -> None:
    u = _users(tmp_path)
    local = u.create("carol", "pw-12345678", email="c@e.com", role="user")
    cfg = OIDCProvisioning(match_existing_by="email")
    assert (
        provision(u, ISS, "s2", {"sub": "s2", "email": "c@e.com", "email_verified": False}, cfg)
        is None
    )
    got = provision(u, ISS, "s2", {"sub": "s2", "email": "c@e.com", "email_verified": True}, cfg)
    assert got is not None and got.id == local.id and got.oidc_sub == "s2"


def test_match_refuses_privileged_and_cross_issuer(tmp_path: Path) -> None:
    u = _users(tmp_path)
    u.create("root", "pw-12345678", email="admin@e.com", role="root")
    by_email = OIDCProvisioning(match_existing_by="email")
    by_name = OIDCProvisioning(match_existing_by="username")
    # verified-email or username match to a root/admin account is refused (no privilege by claim)
    ce = {"sub": "x1", "email": "admin@e.com", "email_verified": True}
    assert provision(u, ISS, "x1", ce, by_email) is None
    assert provision(u, ISS, "x2", {"sub": "x2", "preferred_username": "root"}, by_name) is None
    # an account bound to (idpA, subX) is not rebound by a different issuer asserting subX
    u.create("dana", None, email="d@e.com", oidc_iss="https://idpA", oidc_sub="subX", role="user")
    cross = {"sub": "subX", "email": "d@e.com", "email_verified": True}
    assert provision(u, "https://idpB", "subX", cross, by_email) is None


def test_email_match_refuses_cross_subject_takeover(tmp_path: Path) -> None:
    u = _users(tmp_path)
    u.create("dave", None, email="d@e.com", oidc_iss=ISS, oidc_sub="sub-A")
    cfg = OIDCProvisioning(match_existing_by="email")
    claims = {"sub": "sub-B", "email": "d@e.com", "email_verified": True}
    assert provision(u, ISS, "sub-B", claims, cfg) is None  # different sub can't claim the email


def test_group_claim_maps_admin(tmp_path: Path) -> None:
    u = _users(tmp_path)
    cfg = OIDCProvisioning(auto_register=True, group_claim="groups", admin_group="cm-admins")
    claims = {"sub": "g1", "preferred_username": "erin", "groups": ["staff", "cm-admins"]}
    got = provision(u, ISS, "g1", claims, cfg)
    assert got is not None and got.role == "admin"


def test_admin_email_allowlist_maps_admin(tmp_path: Path) -> None:
    u = _users(tmp_path)
    cfg = OIDCProvisioning(auto_register=True, admin_emails=("boss@e.com",))
    claims = {"sub": "a1", "email": "boss@e.com", "email_verified": True}
    got = provision(u, ISS, "a1", claims, cfg)
    assert got is not None and got.role == "admin"


def test_disabled_user_is_denied(tmp_path: Path) -> None:
    u = _users(tmp_path)
    created = u.create("frank", None, oidc_iss=ISS, oidc_sub="f1")
    u.set_active(created.id, False)
    assert provision(u, ISS, "f1", {"sub": "f1"}, OIDCProvisioning()) is None


def test_username_match_links(tmp_path: Path) -> None:
    u = _users(tmp_path)
    local = u.create("gail", "pw-12345678")
    cfg = OIDCProvisioning(match_existing_by="username")
    got = provision(u, ISS, "s9", {"sub": "s9", "preferred_username": "gail"}, cfg)
    assert got is not None and got.id == local.id and got.oidc_sub == "s9"
