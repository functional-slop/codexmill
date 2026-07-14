"""OIDC config + Authlib client. Config resolves from the persistent store first (set via the
/admin GUI), then env as a fallback for headless deploys. The session secret is separate (store-
level), so OIDC can be toggled at runtime without touching cookie signing. See ADR 0008/0009."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from codexmill.web.store import ConfigStore


@dataclass(frozen=True)
class OIDCConfig:
    issuer: str
    client_id: str
    client_secret: str
    scope: str = "openid email profile"

    @property
    def metadata_url(self) -> str:
        return self.issuer.rstrip("/") + "/.well-known/openid-configuration"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> OIDCConfig | None:
        if d.get("issuer") and d.get("client_id") and d.get("client_secret"):
            return cls(
                issuer=str(d["issuer"]),
                client_id=str(d["client_id"]),
                client_secret=str(d["client_secret"]),
                scope=str(d.get("scope") or "openid email profile"),
            )
        return None

    @classmethod
    def from_env(cls) -> OIDCConfig | None:
        return cls.from_dict(
            {
                "issuer": os.environ.get("CODEXMILL_OIDC_ISSUER"),
                "client_id": os.environ.get("CODEXMILL_OIDC_CLIENT_ID"),
                "client_secret": os.environ.get("CODEXMILL_OIDC_CLIENT_SECRET"),
                "scope": os.environ.get("CODEXMILL_OIDC_SCOPE"),
            }
        )


def resolve_oidc(store: ConfigStore) -> OIDCConfig | None:
    """Store (GUI-set) wins; env is the fallback for headless deploys."""
    return OIDCConfig.from_dict(store.get_oidc()) or OIDCConfig.from_env()


def build_oauth(oidc: OIDCConfig) -> Any:
    """Register an Authlib OAuth client named 'oidc'. Discovery is lazy (no network at import)."""
    from authlib.integrations.starlette_client import OAuth

    oauth = OAuth()
    oauth.register(
        name="oidc",
        server_metadata_url=oidc.metadata_url,
        client_id=oidc.client_id,
        client_secret=oidc.client_secret,
        client_kwargs={"scope": oidc.scope},
    )
    return oauth
