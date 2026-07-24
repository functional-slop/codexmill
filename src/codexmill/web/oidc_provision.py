"""OIDC user provisioning + linking (ADR 0025), as a pure function so the security-critical identity
logic is unit-testable without a live IdP.

Identity is the immutable ``(iss, sub)`` pair. An unknown subject may be linked to a pre-existing
account only per ``match_existing_by`` — by a *verified* email, or by username — and never to an
email already bound to a different subject. If nothing matches, the login is created
(``auto_register``) or denied. Role comes from the IdP group claim, then the admin-email allowlist,
else ``user``; an existing ``root`` is never downgraded."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from codexmill.web.models import User
from codexmill.web.users import Users


@dataclass(frozen=True)
class OIDCProvisioning:
    auto_register: bool = False
    match_existing_by: str = ""  # "" (sub only) | "email" | "username"
    group_claim: str = ""
    admin_group: str = ""
    admin_emails: tuple[str, ...] = field(default_factory=tuple)


def _email_verified(claims: dict[str, Any]) -> bool:
    ev = claims.get("email_verified")
    return ev is True or (isinstance(ev, str) and ev.lower() == "true")


def _verified_email(claims: dict[str, Any]) -> str | None:
    """The email claim, normalized (trimmed + lowercased), only if the IdP marked it verified."""
    email = claims.get("email")
    return str(email).strip().lower() if email and _email_verified(claims) else None


def _role(claims: dict[str, Any], cfg: OIDCProvisioning) -> str:
    if cfg.group_claim and cfg.admin_group:
        groups = claims.get(cfg.group_claim)
        if isinstance(groups, list) and cfg.admin_group in [str(g) for g in groups]:
            return "admin"
    email = _verified_email(claims)
    if email and email in {e.strip().lower() for e in cfg.admin_emails}:
        return "admin"
    return "user"


def _unique_username(users: Users, claims: dict[str, Any], sub: str) -> str:
    base = str(claims.get("preferred_username") or claims.get("email") or f"user-{sub}")[:100]
    name, i = base, 1
    while users.by_username(name) is not None:
        i += 1
        name = f"{base}-{i}"
    return name


def provision(
    users: Users, iss: str, sub: str, claims: dict[str, Any], cfg: OIDCProvisioning
) -> User | None:
    """Return the User for a validated OIDC login (creating or linking per policy), or ``None`` to
    deny. Callers must have already validated the token (Authlib); this only maps a trusted identity
    to a local account."""
    user = users.by_oidc(iss, sub)
    if user is None:
        existing = _match_existing(users, iss, sub, claims, cfg)
        if existing is not None:
            users.link_oidc(existing.id, iss, sub)
            user = users.by_id(existing.id)
        elif cfg.auto_register:
            user = users.create(
                _unique_username(users, claims, sub),
                None,
                role=_role(claims, cfg),
                email=_verified_email(claims),
                oidc_iss=iss,
                oidc_sub=sub,
            )
        else:
            return None  # unmatched and auto-register off -> deny
    if user is None or not user.is_active:
        return None
    # Promote to admin when the IdP mapping says so; never auto-DEMOTE — that would silently strip a
    # locally-granted admin on their next SSO login. Demotion is an explicit admin action.
    if _role(claims, cfg) == "admin" and user.role == "user":
        users.set_role(user.id, "admin")
        user = users.by_id(user.id)
    return user


def _match_existing(
    users: Users, iss: str, sub: str, claims: dict[str, Any], cfg: OIDCProvisioning
) -> User | None:
    """Link an unknown subject to a pre-existing account, per policy. NEVER links to a privileged
    account (role is granted explicitly, not by a matchable/unverified claim), and is issuer-aware:
    it attaches only to an account with no OIDC identity, or the exact same (iss, sub) — never a
    cross-issuer rebind."""
    cand: User | None = None
    if cfg.match_existing_by == "email":
        email = _verified_email(claims)
        cand = users.by_email(email) if email else None
    elif cfg.match_existing_by == "username":
        username = claims.get("preferred_username")
        cand = users.by_username(str(username)) if username else None
    if cand is None or cand.role in ("root", "admin"):
        return None
    if (cand.oidc_iss is None and cand.oidc_sub is None) or (
        cand.oidc_iss == iss and cand.oidc_sub == sub
    ):
        return cand
    return None
