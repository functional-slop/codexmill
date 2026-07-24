"""FastAPI app (factory). The browser talks only to this server; the server talks to the LLM.
Keys in the generate form are used per-request and never stored.

Auth is MANDATORY (ADR 0024). A fresh instance is CLOSED: only `/api/me` and `/api/auth/setup` work
until a local admin account (username + argon2id password) is created in first-run onboarding. After
that every route needs a session. There is no open bootstrap.

OIDC (ADR 0008/0009) remains optional on top: SSO for a shared instance, configured in the /admin
GUI or via env, toggleable at runtime. Every authenticated user (local or OIDC-provisioned) is a
row; admin = a user with the root/admin role (OIDC roles map from an IdP group or the admin-email
allowlist), or the CODEXMILL_SETUP_TOKEN break-glass."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from codexmill.config import Settings
from codexmill.export import (
    DOCX_MEDIA_TYPE,
    series_to_docx,
    series_to_obsidian_zip,
    to_docx,
    to_obsidian_zip,
)
from codexmill.llm import BackendError, Usage, make_backend
from codexmill.pipeline import STAGE_LABELS, build, build_iter, regenerate
from codexmill.render import render_bible, render_series, slugify
from codexmill.schemas import SeriesBible, SeriesSpec, Spec, StoryBible, StorySeed
from codexmill.series import build_series, build_series_iter, regenerate_book
from codexmill.stages import surprise as surprise_stage
from codexmill.web.auth import OIDCConfig, build_oauth, resolve_oidc
from codexmill.web.library import BibleSummary, Library
from codexmill.web.model_filter import (
    display_name,
    filter_models,
    human_size,
    is_large,
    normalize_model_id,
)
from codexmill.web.models import User
from codexmill.web.oidc_provision import OIDCProvisioning, provision
from codexmill.web.providers import catalog as providers_catalog
from codexmill.web.providers import get_provider
from codexmill.web.store import ConfigStore
from codexmill.web.users import Users

_STATIC = Path(__file__).parent / "static"
log = logging.getLogger("codexmill.web")


def _sso_reason(exc: Exception) -> str:
    """A short, user-safe reason for an SSO failure (no stack traces, no secrets). Authlib's
    OAuthError carries a machine `error` code (e.g. invalid_request, access_denied); prefer its
    human description when present, else fall back to a generic message."""
    desc = getattr(exc, "description", None) or getattr(exc, "error", None)
    text = str(desc or "sign-in failed").strip()
    return text[:140] if text else "sign-in failed"


class _LLMOverrides(BaseModel):
    """Per-request engine overrides shared by generate + regenerate (all optional; fall back to
    the saved server default, then env)."""

    backend: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None


class GenerateRequest(_LLMOverrides):
    spec: Spec


class SeriesRequest(_LLMOverrides):
    spec: SeriesSpec


class RegenerateBookRequest(_LLMOverrides):
    book: int  # 1-based index of the book to regenerate


class GenerateResponse(BaseModel):
    id: str
    filename: str
    markdown: str
    usage: Usage = Usage()  # tokens this run used; empty for read-only loads (ADR 0021)
    model: str = ""  # which model produced it, so a token count can be interpreted


class RegenerateRequest(_LLMOverrides):
    stage: str  # one of pipeline.STAGE_LABELS


class OIDCSettings(BaseModel):
    issuer: str = ""
    client_id: str = ""
    client_secret: str = ""  # write-only; blank keeps the stored one
    admin_emails: list[str] = []
    auto_register: bool = False
    match_existing_by: str = ""  # "" (sub only) | "email" | "username"
    group_claim: str = ""
    admin_group: str = ""


class OIDCStatus(BaseModel):
    enabled: bool
    issuer: str
    client_id: str
    has_secret: bool
    admin_emails: list[str]
    source: str  # store | env | none
    auto_register: bool = False
    match_existing_by: str = ""
    group_claim: str = ""
    admin_group: str = ""


class TestRequest(BaseModel):
    issuer: str


class LLMSettings(BaseModel):
    backend: str = ""
    base_url: str = ""
    model: str = ""
    api_key: str = ""  # write-only; blank keeps the stored one
    stage_models: dict[str, str] = {}  # stage label -> model override


class LLMStatus(BaseModel):
    backend: str
    base_url: str
    model: str
    has_key: bool
    configured: bool
    stage_models: dict[str, str]


class SharedAIBody(BaseModel):
    allow_shared_ai: bool  # may non-admin users generate with the server's AI
    # Separate, stricter opt-in: may non-admins use the server AI even when it carries a PAID cloud
    # key (spending the operator's quota)? Default False. Optional on input so old clients work.
    allow_shared_paid_key: bool = False
    server_ai_is_paid: bool = False  # read-only hint for the UI: does the server AI carry a key?


class UserLLMBody(BaseModel):
    """A user's own bring-your-own cloud key (ADR 0027). ``provider`` must be on the allow-list
    (``web.providers``); the base_url is derived server-side from it, never sent by the client."""

    provider: str
    model: str = ""
    api_key: str = ""  # write-only; never read back


class UserLLMStatus(BaseModel):
    has_key: bool
    provider: str
    model: str


class ServerAIChoice(BaseModel):
    """Optional model pick when opting into the server's AI; blank means the instance default."""

    model: str = ""


class _Ping(BaseModel):
    """Tiny schema for the connection test: exercises the real generation path (including the
    chosen model + structured output) so 'Test' can't pass on a key that lists models but can't
    actually generate with them."""

    ok: str = ""


class SetupRequest(BaseModel):
    """First-run creation of the local admin account (ADR 0024)."""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=256)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class RateLimitSettings(BaseModel):
    max_generations: int = 0  # 0 = unlimited (default; single-user/beta instances stay unlimited)
    window_hours: float = 24.0


class RateLimitStatus(BaseModel):
    max_generations: int
    window_hours: float
    enabled: bool
    source: str  # store | env | none


class AuthMethodsBody(BaseModel):
    methods: list[str]  # subset of {"local", "oidc"}


class UserOut(BaseModel):
    id: str
    username: str
    email: str | None
    role: str
    is_active: bool
    permissions: dict[str, Any]
    is_oidc: bool
    last_seen: str | None
    created_at: str


class NewUser(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=256)
    role: str = "user"  # admin | user (a second root can't be created via the API)


class UserPatch(BaseModel):
    # All optional; only the fields actually sent are applied (model_fields_set).
    role: str | None = None
    is_active: bool | None = None
    use_server_engine: bool | None = None
    quota: int | None = None  # per-user generation cap; null = inherit the instance default


class PasswordReset(BaseModel):
    password: str = Field(min_length=8, max_length=256)


class _LoginThrottle:
    """Brute-force guard for the login endpoint (ADR 0024).

    A password form with no throttle is just an offline attack run online. After `limit` failures
    from one client address inside `window` seconds, further attempts are refused until the window
    rolls off. Keyed by client address, not username, so an attacker can't lock a legitimate user
    out by spamming their name. In-memory (single-process self-host); state resets on restart,
    which is fine — it only ever costs an attacker time."""

    def __init__(self, limit: int = 5, window: float = 300.0) -> None:
        self._limit = limit
        self._window = window
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> list[float]:
        recent = [t for t in self._fails.get(key, []) if now - t < self._window]
        if recent:
            self._fails[key] = recent
        else:
            self._fails.pop(key, None)
        return recent

    def check(self, key: str) -> None:
        """Raise 429 if this client is currently locked out."""
        now = time.monotonic()
        with self._lock:
            recent = self._prune(key, now)
            if len(recent) >= self._limit:
                retry = int(self._window - (now - min(recent))) + 1
                raise HTTPException(
                    status_code=429,
                    detail="too many failed sign-in attempts; try again later",
                    headers={"Retry-After": str(max(retry, 1))},
                )

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._prune(key, now)
            self._fails.setdefault(key, []).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


def _migrate_legacy_users(store: ConfigStore, users: Users, library: Library) -> None:
    """One-time move of the JSON account(s) into the users table (first becomes root), re-keying
    their bibles (owned by the pre-auth ``local`` string or the username) onto the new stable user
    id. Idempotent and crash-safe: keyed on the legacy JSON still being present (cleared only at the
    end), and per-account by username, so a re-run after a mid-migration crash finishes the job."""
    legacy = store.legacy_users()
    if not legacy:
        return
    root_id: str | None = None
    for i, (username, phash) in enumerate(legacy):
        row = users.by_username(username) or users.create_with_hash(
            username, phash, role="root" if i == 0 else "admin"
        )
        library.rekey_owner(username, row.id)  # a no-op if already re-keyed
        if root_id is None:
            root_id = row.id
    if root_id is not None:
        library.rekey_owner("local", root_id)  # bibles saved before auth existed
    store.clear_legacy_users()


def create_app(store: ConfigStore | None = None, library: Library | None = None) -> FastAPI:
    store = store or ConfigStore()
    library = library or Library()
    users = Users(library.engine)
    _migrate_legacy_users(store, users, library)
    # Disable the interactive API docs / OpenAPI schema: a "closed" instance (ADR 0024) shouldn't
    # publish its full route map to unauthenticated callers.
    app = FastAPI(title="CodexMill", docs_url=None, redoc_url=None, openapi_url=None)
    # Session cookie: lax SameSite blocks cross-site POSTs (CSRF on /api/generate etc.); set
    # CODEXMILL_HTTPS_ONLY=1 behind TLS so the cookie is Secure-only. max_age is capped (default 7
    # days) and configurable; combined with the per-identity session epoch, a stale/stolen cookie
    # stops working after logout or the window elapses.
    _max_age = 7 * 24 * 3600
    with contextlib.suppress(ValueError):
        _max_age = int(os.environ.get("CODEXMILL_SESSION_MAX_AGE", _max_age))
    app.add_middleware(
        SessionMiddleware,
        secret_key=store.session_secret(),
        same_site="lax",
        https_only=os.environ.get("CODEXMILL_HTTPS_ONLY", "").lower() in {"1", "true", "yes"},
        max_age=_max_age,
    )

    # The app loads no external resources; a strict CSP enforces that (and blocks exfiltration),
    # while still allowing the bundled inline script/style. Applied to every response.
    _SECURITY_HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; connect-src 'self'; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'"
        ),
    }

    @app.middleware("http")
    async def _security_headers(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        for header, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response

    oauth_cache: dict[tuple[str, str, str, str], Any] = {}
    login_throttle = _LoginThrottle()

    def current_oidc() -> OIDCConfig | None:
        return resolve_oidc(store)

    def oauth_for(oidc: OIDCConfig) -> Any:
        sig = (oidc.issuer, oidc.client_id, oidc.client_secret, oidc.scope)
        if sig not in oauth_cache:
            oauth_cache.clear()  # keep only the current config's client
            oauth_cache[sig] = build_oauth(oidc)
        return oauth_cache[sig]

    def effective_admin_emails() -> list[str]:
        env = os.environ.get("CODEXMILL_ADMIN_EMAILS", "")
        return store.admin_emails() or [e.strip() for e in env.split(",") if e.strip()]

    def _oidc_provisioning() -> OIDCProvisioning:
        cfg = store.get_oidc()
        return OIDCProvisioning(
            auto_register=bool(cfg.get("auto_register")),
            match_existing_by=str(cfg.get("match_existing_by") or ""),
            group_claim=str(cfg.get("group_claim") or ""),
            admin_group=str(cfg.get("admin_group") or ""),
            admin_emails=tuple(effective_admin_emails()),
        )

    def status() -> OIDCStatus:
        stored = store.get_oidc()
        env = OIDCConfig.from_env()
        source = "store" if OIDCConfig.from_dict(stored) else ("env" if env else "none")
        return OIDCStatus(
            enabled=current_oidc() is not None,
            issuer=str(stored.get("issuer") or (env.issuer if env else "")),
            client_id=str(stored.get("client_id") or (env.client_id if env else "")),
            has_secret=bool(stored.get("client_secret")) or bool(env),
            admin_emails=effective_admin_emails(),
            source=source,
            auto_register=bool(stored.get("auto_register")),
            match_existing_by=str(stored.get("match_existing_by") or ""),
            group_claim=str(stored.get("group_claim") or ""),
            admin_group=str(stored.get("admin_group") or ""),
        )

    def _stage_models(s: dict[str, Any]) -> dict[str, str]:
        raw = s.get("stage_models")
        return {str(k): str(v) for k, v in raw.items() if v} if isinstance(raw, dict) else {}

    def llm_status() -> LLMStatus:
        s = store.get_llm()
        return LLMStatus(
            backend=str(s.get("backend", "")),
            base_url=str(s.get("base_url", "")),
            model=str(s.get("model", "")),
            has_key=bool(s.get("api_key")),
            configured=bool(s),
            stage_models=_stage_models(s),
        )

    def _user_row(request: Request) -> User | None:
        sess = session_user(request)
        return users.by_id(str(sess.get("id"))) if sess else None

    def _user_perms(request: Request) -> dict[str, Any]:
        row = _user_row(request)
        return dict(row.permissions) if row else {}

    def _is_admin_request(request: Request) -> bool:
        row = _user_row(request)
        return row is None or row.role in ("root", "admin")

    def server_ai_configured() -> bool:
        """Whether an admin has ACTUALLY set up the server's AI: a saved config, or an explicit
        ``CODEXMILL_BACKEND``/``BASE_URL``/``MODEL``/``API_KEY`` in the environment (headless
        deploys, and the offline fake backend).

        This is separate from *permission* to use it. Without this check the app would report
        "AI ready" to a user whose instance has no AI at all, then fall through to the built-in
        ``localhost:11434`` default — which on a hosted instance points at nothing."""
        return bool(store.get_llm()) or any(
            os.environ.get(v)
            for v in (
                "CODEXMILL_BACKEND",
                "CODEXMILL_BASE_URL",
                "CODEXMILL_MODEL",
                "CODEXMILL_API_KEY",
            )
        )

    def _server_ai_is_paid() -> bool:
        """True when the shared server AI carries an api_key — using it spends the operator's cloud
        quota. A keyless endpoint (a local Ollama) is free to share; a keyed one is not."""
        return bool(str(store.get_llm().get("api_key") or ""))

    def _shared_ai_permitted(request: Request) -> bool:
        """Whether this account is ALLOWED to use the server's AI (says nothing about whether one
        exists). Admins always are; a non-admin only when the instance shares its AI AND their
        per-user switch is on AND — critically — using it would not silently spend the operator's
        paid cloud key. A keyed (paid) server AI is off-limits to non-admins unless the operator has
        EXPLICITLY opted into sharing a paid key (default off). This is the structural guard against
        billing testers to the operator's own Gemini/OpenAI key."""
        row = _user_row(request)
        if row is None or row.role in ("root", "admin"):
            return True
        per_user = row.permissions.get("use_server_engine", True) is not False
        if not (store.allow_shared_ai() and per_user):
            return False
        # A keyed (paid) server AI is off-limits to a non-admin unless the operator opted in.
        return not (_server_ai_is_paid() and not store.allow_shared_paid_key())

    def _can_use_shared_ai(request: Request) -> bool:
        """Permitted AND actually configured — i.e. a generation would really work."""
        return _shared_ai_permitted(request) and server_ai_configured()

    def resolve_settings(request: Request, req: _LLMOverrides) -> Settings:
        """Engine config for a generation. Resolution precedence (ADR 0027):

        1. the caller's own bring-your-own cloud key, if set — the base_url comes from the provider
           allow-list, so even here the endpoint is never client-supplied;
        2. otherwise the shared server AI, if this account may use it (else 403).

        Per-request ``base_url``/``api_key``/``backend`` overrides are an ADMIN-only convenience
        (the sample/testing path). Honouring them for a non-admin would let any signed-in user
        point the server at an arbitrary endpoint (SSRF into the host's network) and exfiltrate the
        shared key by supplying a ``base_url`` with no key of their own. So a non-admin gets only
        the harmless ``model`` name override; the URL/key/backend always come from the stored
        config."""
        row = _user_row(request)
        if row is not None:
            own = users.user_llm_resolved(row.id)
            if own is not None:
                return Settings.from_overrides(
                    backend="openai",
                    base_url=own["base_url"],
                    model=req.model or own.get("model") or None,
                    api_key=own["api_key"],
                )
        if not _shared_ai_permitted(request):
            raise HTTPException(
                status_code=403,
                detail="You don't have access to generate on this instance. Ask an admin.",
            )
        admin = _is_admin_request(request)
        ov_backend = req.backend if admin else None
        ov_base = req.base_url if admin else None
        ov_key = req.api_key if admin else None
        if not server_ai_configured() and not (ov_backend or ov_base or ov_key):
            # Distinct from "not allowed": they may use the server's AI, there just isn't one, and
            # nothing was supplied per-request either. Without this the run would fall through to
            # the built-in localhost default and fail with an obscure connection error.
            raise HTTPException(
                status_code=409,
                detail=(
                    "This server doesn't have an AI set up yet. Add your own provider key under "
                    '"Your AI", or ask an admin to configure the server\'s AI.'
                ),
            )
        s = store.get_llm()
        # A user on the server's AI may pick among its models (e.g. a local Ollama's), falling back
        # to the instance default when they haven't chosen.
        chosen = users.server_model(row.id) if row is not None else ""
        return Settings.from_overrides(
            backend=ov_backend or (str(s["backend"]) if s.get("backend") else None),
            base_url=ov_base or (str(s["base_url"]) if s.get("base_url") else None),
            model=req.model or chosen or (str(s["model"]) if s.get("model") else None),
            api_key=ov_key or (str(s["api_key"]) if s.get("api_key") else None),
        )

    def request_stage_models(request: Request, req: _LLMOverrides) -> dict[str, str]:
        # A request-level model overrides all stages uniformly. A BYO user has a single model, so
        # the shared per-stage overrides don't apply to them. Otherwise use the server's per-stage
        # models.
        if req.model:
            return {}
        row = _user_row(request)
        if row is not None and users.user_llm_resolved(row.id) is not None:
            return {}
        return _stage_models(store.get_llm())

    def _probe_models(base_url: str, api_key: str) -> dict[str, Any]:
        """List an OpenAI-compatible endpoint's models for the 'test connection' button."""
        import httpx

        base = (base_url or "").rstrip("/")
        if not base:
            return {"ok": False, "error": "no base URL set"}
        try:
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            r = httpx.get(base + "/models", headers=headers, timeout=8.0)
            r.raise_for_status()
            # Normalise ids to the form the chat endpoint accepts (Gemini lists "models/…" but
            # generates on the bare id) so a picked model can't 404 on a namespace mismatch.
            models = [normalize_model_id(m.get("id") or "") for m in r.json().get("data", [])][:25]
            return {"ok": True, "models": [m for m in models if m]}
        except Exception as exc:  # report any failure back to the UI
            return {"ok": False, "error": str(exc)[:200]}

    def effective_rate_limit() -> tuple[int, float, str]:
        """Resolve the generation quota: stored value > env > off. Returns
        ``(max_generations, window_hours, source)``; ``max_generations <= 0`` means unlimited."""
        stored = store.get_rate_limit()
        if stored:
            return (
                int(stored.get("max_generations", 0) or 0),
                float(stored.get("window_hours", 24.0) or 24.0),
                "store",
            )
        env_max = os.environ.get("CODEXMILL_MAX_GENERATIONS")
        if env_max:
            with contextlib.suppress(ValueError):
                window = float(os.environ.get("CODEXMILL_RATE_WINDOW_HOURS", "24") or 24)
                return (int(env_max), window, "env")
        return (0, 24.0, "none")

    def rate_limit_status() -> RateLimitStatus:
        max_gen, window, source = effective_rate_limit()
        return RateLimitStatus(
            max_generations=max_gen,
            window_hours=window,
            enabled=max_gen > 0,
            source=source,
        )

    def enforce_quota(request: Request) -> None:
        """Consume one generation slot for the caller; raise 429 if the quota is exhausted. Uses the
        user's per-user quota override when set, else the instance quota (0 = unlimited). See ADR
        0022."""
        owner = current_owner(request)
        max_gen, window, _ = effective_rate_limit()
        user_quota = _user_perms(request).get("quota")
        if isinstance(user_quota, int):
            max_gen = user_quota  # per-user override wins (0 = unlimited for this user)
        if max_gen <= 0:
            return
        allowed, _used = library.try_consume(owner, max_gen, window)
        if not allowed:
            hours = int(window) if window == int(window) else window
            raise HTTPException(
                status_code=429,
                detail=(
                    f"You've reached the limit of {max_gen} generations per {hours} hours "
                    "on this instance. Please try again later."
                ),
                headers={"Retry-After": str(int(window * 3600))},
            )

    def needs_setup() -> bool:
        """True on a brand-new instance: no local account and no OIDC. Only `/api/me` and
        `/api/auth/setup` work in this state — everything else 401s until an admin exists."""
        return not users.has_users() and current_oidc() is None

    def session_user(request: Request) -> dict[str, Any] | None:
        """The authenticated session. Every user (local or OIDC) is a row, so the session holds the
        user id + epoch; a session whose user is gone/disabled or whose epoch no longer matches is
        rejected, so a signed-but-revoked cookie can't be replayed."""
        user = request.session.get("user")
        if not (isinstance(user, dict) and user):
            return None
        row = users.by_id(str(user.get("id") or ""))
        if row is None or not row.is_active or user.get("epoch") != row.session_epoch:
            return None
        return user

    def _sign_in_row(request: Request, row: User) -> None:
        """Store a session keyed on the stable user id, stamped with the row's current epoch."""
        request.session["user"] = {
            "kind": "oidc" if row.oidc_sub else "local",
            "id": row.id,
            "username": row.username,
            "role": row.role,
            "epoch": row.session_epoch,
        }

    async def require_user(request: Request) -> Any:
        """Auth is ALWAYS required (ADR 0024). Before first-run setup nothing is reachable, so an
        un-configured instance is closed rather than open."""
        if needs_setup():
            raise HTTPException(status_code=401, detail="setup required: create an admin account")
        user = session_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="authentication required")
        return user

    def current_owner(request: Request) -> str:
        """Whose library a request reads/writes: the authenticated user's stable id."""
        return str((session_user(request) or {}).get("id") or "local")

    async def require_admin(request: Request) -> None:
        # Break-glass token is HEADER-only: accepting it in the query string would write the secret
        # into server/proxy access logs and browser history. The admin UI has a paste-in field.
        setup = os.environ.get("CODEXMILL_SETUP_TOKEN")
        provided = request.headers.get("X-Setup-Token")
        if setup and provided and secrets.compare_digest(provided, setup):
            return  # break-glass
        if needs_setup():
            raise HTTPException(status_code=401, detail="setup required: create an admin account")
        user = session_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="authentication required")
        # Admin is role-based now (local + OIDC users are both rows). Fetch the live role so a
        # demotion takes effect immediately, not just at the demoted user's next login.
        row = users.by_id(str(user.get("id") or ""))
        if row is not None and row.role in ("root", "admin"):
            return
        raise HTTPException(status_code=403, detail="admin access required")

    @app.get("/auth/login")
    async def login(request: Request) -> Any:
        oidc = current_oidc()
        if oidc is None or "oidc" not in store.get_auth_methods():
            raise HTTPException(status_code=404, detail="OIDC is not configured")
        return await oauth_for(oidc).oidc.authorize_redirect(
            request, request.url_for("auth_callback")
        )

    @app.get("/auth/callback", name="auth_callback")
    async def auth_callback(request: Request) -> RedirectResponse:
        oidc = current_oidc()
        if oidc is None or "oidc" not in store.get_auth_methods():
            raise HTTPException(status_code=404, detail="OIDC is not configured")
        # The IdP may hand the browser back here with an error (?error=...) or the token exchange /
        # state / nonce checks may fail. None of that is a server fault, so it must never 500 — send
        # the user back to the login screen with a short reason, not a stack trace.
        try:
            token = await oauth_for(oidc).oidc.authorize_access_token(request)
        except Exception as exc:  # authlib OAuthError, state/nonce mismatch, network, etc.
            log.warning("OIDC callback failed: %s", exc)
            return RedirectResponse(url="/?sso_error=" + quote_plus(_sso_reason(exc)))
        claims = dict(token.get("userinfo") or {})
        sub = str(claims.get("sub") or "")
        if not sub:
            return RedirectResponse(url="/?sso_error=" + quote_plus("no account identity returned"))
        iss = str(claims.get("iss") or oidc.issuer)
        # Authlib has validated the token (signature / aud / iss / exp / nonce); map the trusted
        # identity to a local account per policy (email matching honors email_verified).
        user = provision(users, iss, sub, claims, _oidc_provisioning())
        if user is None:
            return RedirectResponse(
                url="/?sso_error=" + quote_plus("this account is not permitted here")
            )
        request.session["oidc_id_token"] = str(
            token.get("id_token") or ""
        )  # for RP-initiated logout
        _sign_in_row(request, user)
        return RedirectResponse(url="/")

    def _revoke_session(request: Request) -> None:
        user = request.session.get("user")
        if isinstance(user, dict) and user.get("id"):
            users.rotate_epoch(str(user["id"]))
        request.session.pop("user", None)

    # ---- local account auth (ADR 0024) ----------------------------------------------
    @app.post("/api/auth/setup")
    def auth_setup(body: SetupRequest, request: Request) -> dict[str, Any]:
        """Create the FIRST local admin account and sign in. Only reachable during genuine first-run
        (no account AND no OIDC) — an OIDC-configured instance is NOT open for account creation, and
        setup refuses once any account exists, so this can never be used to take over an instance.
        If CODEXMILL_SETUP_TOKEN is set, it must be supplied (guards the first-run land-grab)."""
        if not needs_setup():
            raise HTTPException(status_code=409, detail="setup is not available on this instance")
        token = os.environ.get("CODEXMILL_SETUP_TOKEN")
        if token:
            provided = request.headers.get("X-Setup-Token")
            if not provided or not secrets.compare_digest(provided, token):
                raise HTTPException(status_code=403, detail="a setup token is required")
        username = body.username.strip()
        if not username:
            raise HTTPException(status_code=422, detail="username required")
        row = users.create(username, body.password, role="root")
        _sign_in_row(request, row)
        return {"ok": True, "username": username}

    @app.post("/api/auth/login")
    def auth_login(body: LoginRequest, request: Request) -> dict[str, Any]:
        if "local" not in store.get_auth_methods():
            raise HTTPException(
                status_code=403, detail="password login is disabled on this instance"
            )
        who = request.client.host if request.client else "unknown"
        login_throttle.check(who)  # 429 while locked out from repeated failures
        row = users.verify(body.username.strip(), body.password)
        if row is None:
            login_throttle.record_failure(who)
            # Same message either way — never reveal whether the username exists. (verify also burns
            # a dummy hash on unknown/disabled users so timing doesn't leak it.)
            raise HTTPException(status_code=401, detail="incorrect username or password")
        login_throttle.reset(who)
        _sign_in_row(request, row)
        return {"ok": True, "username": row.username}

    @app.post("/api/auth/logout")
    def auth_logout(request: Request) -> dict[str, Any]:
        _revoke_session(request)
        return {"ok": True}

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/me")
    def me(request: Request) -> dict[str, Any]:
        enabled = current_oidc() is not None
        user = session_user(request)
        # Live role (not the session copy): a demotion must hide the admin UI immediately, and the
        # frontend gates the Settings page + nav link on this flag (defense in depth — every admin
        # API also enforces the role server-side).
        row = users.by_id(str((user or {}).get("id") or "")) if user else None
        is_admin = bool(row and row.role in ("root", "admin"))
        # A personal bring-your-own key (ADR 0027) lets a user generate on their own dime,
        # independent of whether the instance shares its AI.
        has_own_key = bool(row and users.user_llm_resolved(row.id) is not None)
        server_ai = store.get_llm()
        server_ready = server_ai_configured()
        # Permission to use the server's AI, separate from whether one EXISTS. Reporting "ready" on
        # permission alone is how a user ends up staring at a form backed by nothing. Uses the one
        # gated helper so the paid-key guard (a non-admin can't use a keyed server AI unless the
        # operator opted in) shows up in the UI, not just at generation time.
        shared_permitted = _shared_ai_permitted(request)
        shared_usable = shared_permitted and server_ready
        can_generate = bool(has_own_key or shared_usable)
        # Which AI a generation would actually use, so the UI can show it and the owner can confirm
        # their own key is in effect.
        ai_source = "own" if has_own_key else ("server" if shared_usable else "none")
        # A non-admin chooses their AI once (this server's, or their own key). Saving a key counts
        # as choosing; admins configure the server AI in Settings instead.
        ai_onboarded = bool(
            is_admin or has_own_key or (row and row.permissions.get("ai_onboarded") is True)
        )
        return {
            "oidc_enabled": enabled,
            "is_admin": is_admin,
            "role": (row.role if row else None),
            "can_generate": can_generate,
            "has_server_ai": server_ready,
            "has_own_key": has_own_key,
            "ai_source": ai_source,
            "shared_ai_permitted": shared_permitted,
            "ai_onboarded": ai_onboarded,
            # Model name only, and only to someone allowed to use it — so a user can see what
            # they'd be generating with. Reflects THEIR pick when they've made one, else the
            # instance default. NEVER the server's base_url (internal) or its key.
            "server_ai_model": (
                (
                    (users.server_model(row.id) if row is not None else "")
                    or str(server_ai.get("model") or "")
                )
                if shared_permitted
                else ""
            ),
            # first run: no local account and no OIDC -> the UI must create an admin first
            "needs_setup": needs_setup(),
            # first-run setup requires the break-glass token when the operator set one (guards a
            # LAN-exposed instance against a setup land-grab before the owner finishes onboarding)
            "setup_requires_token": needs_setup() and bool(os.environ.get("CODEXMILL_SETUP_TOKEN")),
            "authenticated": bool(user),
            "auth_methods": store.get_auth_methods(),
            "username": (user or {}).get("username") or (user or {}).get("email") or "",
            "user": user,
            "source_url": os.environ.get("CODEXMILL_SOURCE_URL", ""),
            "feedback_email": os.environ.get("CODEXMILL_FEEDBACK_EMAIL", ""),
            # default Ollama URL the Settings page pre-fills; a container sets this to
            # http://host.docker.internal:11434/v1 so local Ollama works out of the box.
            "ollama_url": os.environ.get("CODEXMILL_OLLAMA_URL", "http://localhost:11434/v1"),
        }

    @app.get("/admin")
    def admin_page() -> FileResponse:
        return FileResponse(_STATIC / "admin.html")

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest() -> FileResponse:
        # explicit route so the PWA manifest serves with the correct content-type
        return FileResponse(
            _STATIC / "manifest.webmanifest", media_type="application/manifest+json"
        )

    @app.get("/api/admin/oidc", response_model=OIDCStatus)
    def get_oidc(_admin: None = Depends(require_admin)) -> OIDCStatus:
        return status()

    @app.put("/api/admin/oidc", response_model=OIDCStatus)
    def put_oidc(body: OIDCSettings, _admin: None = Depends(require_admin)) -> OIDCStatus:
        current = store.get_oidc()
        secret = body.client_secret or str(current.get("client_secret", ""))
        if body.issuer or body.client_id or secret:
            store.set_oidc(
                {
                    "issuer": body.issuer,
                    "client_id": body.client_id,
                    "client_secret": secret,
                    "scope": current.get("scope", "openid email profile"),
                    "auto_register": body.auto_register,
                    "match_existing_by": body.match_existing_by,
                    "group_claim": body.group_claim,
                    "admin_group": body.admin_group,
                }
            )
        else:
            store.set_oidc({})  # clearing all fields disables OIDC
        store.set_admin_emails([e.strip() for e in body.admin_emails if e.strip()])
        oauth_cache.clear()
        return status()

    @app.post("/api/admin/oidc/test")
    def test_oidc(body: TestRequest, _admin: None = Depends(require_admin)) -> dict[str, Any]:
        import httpx

        url = body.issuer.rstrip("/") + "/.well-known/openid-configuration"
        try:
            r = httpx.get(url, timeout=8.0)
            r.raise_for_status()
            doc = r.json()
            return {"ok": True, "authorization_endpoint": doc.get("authorization_endpoint")}
        except Exception as exc:  # report any discovery failure back to the admin UI
            return {"ok": False, "error": str(exc)[:200]}

    @app.get("/api/admin/llm", response_model=LLMStatus)
    def get_llm(_admin: None = Depends(require_admin)) -> LLMStatus:
        return llm_status()

    @app.put("/api/admin/llm", response_model=LLMStatus)
    def put_llm(body: LLMSettings, _admin: None = Depends(require_admin)) -> LLMStatus:
        current = store.get_llm()
        key = body.api_key or str(current.get("api_key", ""))
        if body.backend or body.base_url or body.model or key or body.stage_models:
            store.set_llm(
                {
                    "backend": body.backend,
                    "base_url": body.base_url,
                    "model": body.model,
                    "api_key": key,
                    "stage_models": {k: v for k, v in body.stage_models.items() if v},
                }
            )
        else:
            store.set_llm({})
        return llm_status()

    @app.post("/api/admin/llm/test")
    def test_llm(body: LLMSettings, _admin: None = Depends(require_admin)) -> dict[str, Any]:
        current = store.get_llm()
        stored_base = str(current.get("base_url", ""))
        # NEVER send the stored API key to a base_url the caller just typed in — otherwise "test
        # connection" against an attacker URL exfiltrates the saved provider key. Only reuse the
        # stored key when testing the stored base_url; a new URL must bring its own key.
        if body.base_url and body.base_url.rstrip("/") != stored_base.rstrip("/"):
            key = body.api_key
        else:
            key = body.api_key or str(current.get("api_key", ""))
        return _probe_models(body.base_url or stored_base, key)

    @app.get("/api/admin/rate-limit", response_model=RateLimitStatus)
    def get_rate_limit(_admin: None = Depends(require_admin)) -> RateLimitStatus:
        return rate_limit_status()

    @app.put("/api/admin/rate-limit", response_model=RateLimitStatus)
    def put_rate_limit(
        body: RateLimitSettings, _admin: None = Depends(require_admin)
    ) -> RateLimitStatus:
        if body.max_generations > 0:
            store.set_rate_limit(
                {
                    "max_generations": body.max_generations,
                    "window_hours": body.window_hours if body.window_hours > 0 else 24.0,
                }
            )
        else:
            store.set_rate_limit({})  # 0/negative disables the quota
        return rate_limit_status()

    @app.get("/api/admin/auth-methods", response_model=AuthMethodsBody)
    def get_auth_methods_ep(_admin: None = Depends(require_admin)) -> AuthMethodsBody:
        return AuthMethodsBody(methods=store.get_auth_methods())

    @app.put("/api/admin/auth-methods", response_model=AuthMethodsBody)
    def put_auth_methods_ep(
        body: AuthMethodsBody, _admin: None = Depends(require_admin)
    ) -> AuthMethodsBody:
        methods = [m for m in body.methods if m in ("local", "oidc")]
        # Don't let an admin disable password login unless OIDC actually works, or nobody could
        # sign in (recovery would be the auth_reset CLI). Fail closed on this foot-gun.
        if "local" not in methods and current_oidc() is None:
            raise HTTPException(
                status_code=400,
                detail="Configure and enable OIDC before turning off password login.",
            )
        store.set_auth_methods(methods)
        return AuthMethodsBody(methods=store.get_auth_methods())

    def _shared_ai_body() -> SharedAIBody:
        return SharedAIBody(
            allow_shared_ai=store.allow_shared_ai(),
            allow_shared_paid_key=store.allow_shared_paid_key(),
            server_ai_is_paid=_server_ai_is_paid(),
        )

    @app.get("/api/admin/shared-ai", response_model=SharedAIBody)
    def get_shared_ai(_admin: None = Depends(require_admin)) -> SharedAIBody:
        return _shared_ai_body()

    @app.put("/api/admin/shared-ai", response_model=SharedAIBody)
    def put_shared_ai(body: SharedAIBody, _admin: None = Depends(require_admin)) -> SharedAIBody:
        store.set_allow_shared_ai(body.allow_shared_ai)
        # Spending a paid server key on non-admins is deliberate and defaults off; only an explicit
        # admin request carrying this flag turns it on.
        store.set_allow_shared_paid_key(body.allow_shared_paid_key)
        return _shared_ai_body()

    # ---- per-user bring-your-own AI key (ADR 0027) -----------------------------------
    # All of these act ONLY on the authenticated caller's own row (current_owner). None takes a
    # target user id, and none returns the key — that is the isolation contract.
    @app.get("/api/providers")
    def list_providers(_user: Any = Depends(require_user)) -> list[dict[str, Any]]:
        """The cloud providers a user may bring their own key for; base_url is server-fixed."""
        return providers_catalog()

    @app.get("/api/me/llm", response_model=UserLLMStatus)
    def get_my_llm(request: Request, _user: Any = Depends(require_user)) -> UserLLMStatus:
        return UserLLMStatus(**users.user_llm_status(current_owner(request)))

    @app.put("/api/me/llm", response_model=UserLLMStatus)
    def put_my_llm(
        body: UserLLMBody, request: Request, _user: Any = Depends(require_user)
    ) -> UserLLMStatus:
        try:
            ok = users.set_user_llm(current_owner(request), body.provider, body.model, body.api_key)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not ok:
            raise HTTPException(status_code=404, detail="account not found")
        # Saving a key IS choosing an AI — don't ask again.
        users.set_permission(current_owner(request), "ai_onboarded", True)
        return UserLLMStatus(**users.user_llm_status(current_owner(request)))

    @app.delete("/api/me/llm", status_code=204)
    def delete_my_llm(request: Request, _user: Any = Depends(require_user)) -> Response:
        users.clear_user_llm(current_owner(request))
        return Response(status_code=204)

    @app.get("/api/me/usage")
    def my_usage(request: Request, _user: Any = Depends(require_user)) -> dict[str, Any]:
        """This user's own spend, broken down by model. A bare token total can't be interpreted
        once different runs used different models (the server's, then their own key). Owner-scoped
        like every other /api/me route."""
        rows = library.usage_by_model(current_owner(request))
        return {
            "by_model": rows,
            "total_tokens": sum(int(r["tokens"]) for r in rows),
            "items": sum(int(r["items"]) for r in rows),
        }

    def _server_model_choices() -> list[str]:
        """The server AI's models, curated down to ones that can actually write (``model_filter``).
        Empty when the endpoint can't be reached, in which case the UI just offers the default."""
        s = store.get_llm()
        base = str(s.get("base_url") or "")
        if not base:
            return []
        probe = _probe_models(base, str(s.get("api_key") or ""))
        return filter_models([str(m) for m in probe.get("models") or []]) if probe.get("ok") else []

    def _model_sizes(base_url: str) -> dict[str, int]:
        """On-disk sizes keyed by model id, so the UI can warn that a big model will be slow.

        The OpenAI-compatible ``/v1/models`` carries no size, but an Ollama host exposes it on its
        native ``/api/tags``. Best-effort and Ollama-specific: any failure just means no sizes, and
        the UI simply shows no warning rather than breaking."""
        import httpx

        base = (base_url or "").rstrip("/")
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        try:
            r = httpx.get(base + "/api/tags", timeout=5.0)
            r.raise_for_status()
            models = r.json().get("models") or []
            return {str(m.get("name") or ""): int(m.get("size") or 0) for m in models}
        except Exception:  # not Ollama, unreachable, or unexpected shape -> no size info
            return {}

    @app.get("/api/me/server-models")
    def server_models(request: Request, _user: Any = Depends(require_user)) -> dict[str, Any]:
        """Models the caller may pick when using this server's AI. Only names are returned — never
        the server's base_url or key. Requires permission to use the shared AI."""
        if not _shared_ai_permitted(request):
            raise HTTPException(
                status_code=403,
                detail="This instance hasn't given your account access to its AI.",
            )
        s = store.get_llm()
        default = str(s.get("model") or "")
        choices = _server_model_choices()
        # always offer the admin's configured model, even if the probe failed or filtered it out
        if default and default not in choices:
            choices = [default, *choices]
        sizes = _model_sizes(str(s.get("base_url") or ""))
        return {
            "models": [
                {
                    "id": m,
                    "label": display_name(m),
                    "size": human_size(sizes.get(m)),
                    # a heavy model still works, it's just slow across a multi-stage generation
                    "slow": is_large(sizes.get(m)),
                }
                for m in choices
            ],
            "default": default,
            "current": users.server_model(current_owner(request)) or default,
        }

    @app.post("/api/me/use-server-ai")
    def use_server_ai(
        request: Request, body: ServerAIChoice | None = None, _user: Any = Depends(require_user)
    ) -> dict[str, bool]:
        """Choose this server's shared AI: drop any personal key, optionally pin one of the server's
        models, and mark the one-time AI picker done so the user isn't asked again."""
        if not _shared_ai_permitted(request):
            raise HTTPException(
                status_code=403,
                detail="This instance hasn't given your account access to its AI.",
            )
        uid = current_owner(request)
        model = (body.model if body else "") or ""
        if model:
            # only a model this server actually offers (and that we'd list) may be pinned
            allowed = set(_server_model_choices()) | {str(store.get_llm().get("model") or "")}
            if model not in allowed:
                raise HTTPException(status_code=400, detail="that model isn't available here")
            users.set_server_model(uid, model)
        else:
            users.clear_user_llm(uid)
        users.set_permission(uid, "ai_onboarded", True)
        return {"ok": True}

    @app.post("/api/me/llm/test")
    def test_my_llm(
        body: UserLLMBody, request: Request, _user: Any = Depends(require_user)
    ) -> dict[str, Any]:
        prov = get_provider(body.provider)
        if prov is None:
            raise HTTPException(status_code=400, detail="unknown provider")
        if not body.api_key.strip():
            raise HTTPException(status_code=400, detail="enter a key to test")
        model = (body.model or "").strip() or prov.default_model
        if not model:
            return {"ok": False, "error": "pick a model to test"}
        # A REAL (tiny) generation against the provider's fixed endpoint with the submitted key +
        # chosen model. Listing /models isn't enough: a key can list models it can't actually
        # generate with (wrong tier, model not enabled, quota), which is exactly the "tests fine but
        # can't generate" trap. Never a client-supplied URL, never another user's stored key.
        try:
            backend = make_backend(
                Settings(
                    backend="openai",
                    base_url=prov.base_url,
                    model=model,
                    api_key=body.api_key.strip(),
                    temperature=0.0,
                    timeout=30.0,
                )
            )
            backend.generate("Reply with JSON only.", 'Return {"ok":"ok"}.', _Ping)
        except BackendError as exc:
            return {"ok": False, "model": model, "error": str(exc)[:400]}
        return {"ok": True, "model": model}

    @app.post("/api/me/llm/models")
    def my_llm_models(body: UserLLMBody, _user: Any = Depends(require_user)) -> dict[str, Any]:
        """The models a user's OWN key can actually list, for the bring-your-own model dropdown.
        The base_url comes from the server-side allow-list (never the client), so this can't be
        used to probe an arbitrary endpoint, and the key is used only for this one list call and is
        never stored. Falls back to the provider's curated default list when no key is supplied yet
        or the provider doesn't expose ``/models`` — so the dropdown is never empty."""
        prov = get_provider(body.provider)
        if prov is None:
            raise HTTPException(status_code=400, detail="unknown provider")
        fallback = list(prov.models)
        key = body.api_key.strip()
        if not key:
            return {"models": fallback, "live": False}
        probe = _probe_models(prov.base_url, key)
        if not probe.get("ok"):
            return {"models": fallback, "live": False, "error": probe.get("error")}
        live = filter_models([str(m) for m in probe.get("models") or []])
        # keep the provider's curated picks visible even if the account's /models omitted them
        for m in fallback:
            if m and m not in live:
                live.append(m)
        return {"models": live, "live": True}

    # ---- user management (ADR 0025) --------------------------------------------------
    def _user_out(row: User) -> UserOut:
        return UserOut(
            id=row.id,
            username=row.username,
            email=row.email,
            role=row.role,
            is_active=row.is_active,
            permissions=row.permissions,
            is_oidc=bool(row.oidc_sub),
            last_seen=row.last_seen.isoformat() if row.last_seen else None,
            created_at=row.created_at.isoformat(),
        )

    def _requester(request: Request) -> User | None:
        sess = session_user(request)
        return users.by_id(str(sess.get("id"))) if sess else None

    def _active_root_count() -> int:
        return sum(1 for u in users.list_all() if u.role == "root" and u.is_active)

    def _guard_target(requester: User | None, target: User) -> None:
        """Only root may manage the root account; everyone else is off-limits to a non-root admin's
        reach over root."""
        if target.role == "root" and (requester is None or requester.role != "root"):
            raise HTTPException(status_code=403, detail="only root can manage the root account")

    @app.get("/api/admin/users", response_model=list[UserOut])
    def list_users(_admin: None = Depends(require_admin)) -> list[UserOut]:
        return [_user_out(u) for u in users.list_all()]

    @app.post("/api/admin/users", response_model=UserOut, status_code=201)
    def create_user_ep(body: NewUser, _admin: None = Depends(require_admin)) -> UserOut:
        role = body.role if body.role in ("admin", "user") else "user"  # no second root via API
        if users.by_username(body.username.strip()) is not None:
            raise HTTPException(status_code=409, detail="username already exists")
        return _user_out(users.create(body.username.strip(), body.password, role=role))

    @app.patch("/api/admin/users/{uid}", response_model=UserOut)
    def patch_user_ep(
        uid: str, body: UserPatch, request: Request, _admin: None = Depends(require_admin)
    ) -> UserOut:
        target = users.by_id(uid)
        if target is None:
            raise HTTPException(status_code=404, detail="user not found")
        requester = _requester(request)
        _guard_target(requester, target)
        fields = body.model_fields_set
        if "role" in fields and body.role is not None:
            if body.role not in ("root", "admin", "user"):
                raise HTTPException(status_code=422, detail="invalid role")
            if (body.role == "root" or target.role == "root") and (
                requester is None or requester.role != "root"
            ):
                raise HTTPException(status_code=403, detail="only root can change the root role")
            if target.role == "root" and body.role != "root" and _active_root_count() <= 1:
                raise HTTPException(status_code=400, detail="cannot demote the last root")
            users.set_role(uid, body.role)
        if "is_active" in fields and body.is_active is not None:
            if not body.is_active and requester is not None and requester.id == uid:
                raise HTTPException(status_code=400, detail="cannot disable your own account")
            if not body.is_active and target.role == "root" and _active_root_count() <= 1:
                raise HTTPException(status_code=400, detail="cannot disable the last root")
            users.set_active(uid, body.is_active)
        if "use_server_engine" in fields and body.use_server_engine is not None:
            users.set_permission(uid, "use_server_engine", body.use_server_engine)
        if "quota" in fields:
            users.set_permission(uid, "quota", body.quota)
        row = users.by_id(uid)
        assert row is not None
        return _user_out(row)

    @app.post("/api/admin/users/{uid}/password")
    def reset_user_password_ep(
        uid: str, body: PasswordReset, request: Request, _admin: None = Depends(require_admin)
    ) -> dict[str, bool]:
        target = users.by_id(uid)
        if target is None:
            raise HTTPException(status_code=404, detail="user not found")
        _guard_target(_requester(request), target)
        users.set_password(uid, body.password)
        return {"ok": True}

    @app.delete("/api/admin/users/{uid}", status_code=204)
    def delete_user_ep(
        uid: str, request: Request, _admin: None = Depends(require_admin)
    ) -> Response:
        target = users.by_id(uid)
        if target is None:
            raise HTTPException(status_code=404, detail="user not found")
        requester = _requester(request)
        _guard_target(requester, target)
        if requester is not None and requester.id == uid:
            raise HTTPException(status_code=400, detail="cannot delete your own account")
        if target.role == "root" and _active_root_count() <= 1:
            raise HTTPException(status_code=400, detail="cannot delete the last root")
        users.delete(uid)
        return Response(status_code=204)

    @app.post("/api/surprise", response_model=StorySeed)
    def surprise(
        req: _LLMOverrides, request: Request, _user: Any = Depends(require_user)
    ) -> StorySeed:
        enforce_quota(request)  # it makes a real LLM call; count it toward the quota
        backend = make_backend(resolve_settings(request, req))
        try:
            return surprise_stage.generate(backend)
        except BackendError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/generate", response_model=GenerateResponse)
    def generate(
        req: GenerateRequest, request: Request, _user: Any = Depends(require_user)
    ) -> GenerateResponse:
        enforce_quota(request)
        settings = resolve_settings(request, req)
        backend = make_backend(settings)
        t0 = time.monotonic()
        try:
            bible = build(req.spec, backend, request_stage_models(request, req))
        except BackendError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        bid = library.save(
            current_owner(request),
            bible,
            backend.usage.total_tokens,
            time.monotonic() - t0,
            settings.model,
        )
        return GenerateResponse(
            id=bid,
            filename=f"{slugify(bible.premise.logline)}.md",
            markdown=render_bible(bible),
            usage=backend.usage,
            model=settings.model,
        )

    @app.post("/api/generate/stream")
    def generate_stream(
        req: GenerateRequest, request: Request, _user: Any = Depends(require_user)
    ) -> StreamingResponse:
        owner = current_owner(request)
        enforce_quota(request)  # before the stream opens, so denial is a normal 429
        settings = resolve_settings(request, req)
        backend = make_backend(settings)

        stage_models = request_stage_models(request, req)

        def events() -> Iterator[str]:
            t0 = time.monotonic()
            try:
                for event in build_iter(req.spec, backend, stage_models):
                    if isinstance(event, StoryBible):
                        bid = library.save(
                            owner,
                            event,
                            backend.usage.total_tokens,
                            time.monotonic() - t0,
                            settings.model,
                        )
                        payload = {
                            "done": True,
                            "id": bid,
                            "filename": f"{slugify(event.premise.logline)}.md",
                            "markdown": render_bible(event),
                            "usage": backend.usage.model_dump(),
                            "model": settings.model,
                        }
                    else:
                        stage, index, total = event
                        # usage so far (stages completed before this one) drives a live token count
                        payload = {
                            "stage": stage,
                            "index": index,
                            "total": total,
                            "usage": backend.usage.model_dump(),
                            "model": settings.model,
                        }
                    yield f"data: {json.dumps(payload)}\n\n"
            except BackendError as exc:
                yield f"data: {json.dumps({'error': str(exc)})}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/api/bibles", response_model=list[BibleSummary])
    def list_bibles(request: Request, _user: Any = Depends(require_user)) -> list[BibleSummary]:
        return library.list(current_owner(request))

    @app.get("/api/bibles/{bid}", response_model=GenerateResponse)
    def get_bible(
        bid: str, request: Request, _user: Any = Depends(require_user)
    ) -> GenerateResponse:
        bible = library.get(current_owner(request), bid)
        if bible is None:
            raise HTTPException(status_code=404, detail="not found")
        return GenerateResponse(
            id=bid,
            filename=f"{slugify(bible.premise.logline)}.md",
            markdown=render_bible(bible),
            model=library.model_for(current_owner(request), bid),
        )

    @app.get("/api/bibles/{bid}/export")
    def export_bible(
        bid: str,
        request: Request,
        fmt: str = Query("docx", alias="format"),
        _user: Any = Depends(require_user),
    ) -> Response:
        bible = library.get(current_owner(request), bid)
        if bible is None:
            raise HTTPException(status_code=404, detail="not found")
        slug = slugify(bible.premise.logline)
        if fmt == "docx":
            data, media, name = to_docx(bible), DOCX_MEDIA_TYPE, f"{slug}.docx"
        elif fmt == "obsidian":
            data, media, name = to_obsidian_zip(bible), "application/zip", f"{slug}.zip"
        else:
            raise HTTPException(status_code=400, detail="format must be docx or obsidian")
        return Response(
            content=data,
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.post("/api/bibles/{bid}/regenerate", response_model=GenerateResponse)
    def regenerate_bible(
        bid: str, body: RegenerateRequest, request: Request, _user: Any = Depends(require_user)
    ) -> GenerateResponse:
        owner = current_owner(request)
        existing = library.get(owner, bid)
        if existing is None:
            raise HTTPException(status_code=404, detail="not found")
        if body.stage not in STAGE_LABELS:
            raise HTTPException(status_code=400, detail=f"stage must be one of {STAGE_LABELS}")
        enforce_quota(request)  # after the 404/400 checks, so a bad request doesn't burn a slot
        settings = resolve_settings(request, body)
        backend = make_backend(settings)
        t0 = time.monotonic()
        try:
            updated = regenerate(backend, existing, body.stage, request_stage_models(request, body))
        except BackendError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        library.update(
            owner, bid, updated, backend.usage.total_tokens, time.monotonic() - t0, settings.model
        )
        return GenerateResponse(
            id=bid,
            filename=f"{slugify(updated.premise.logline)}.md",
            markdown=render_bible(updated),
            usage=backend.usage,
            model=settings.model,
        )

    @app.delete("/api/bibles/{bid}", status_code=204)
    def delete_bible(bid: str, request: Request, _user: Any = Depends(require_user)) -> Response:
        if not library.delete(current_owner(request), bid, "book"):
            raise HTTPException(status_code=404, detail="not found")
        return Response(status_code=204)

    def _series_response(
        bid: str, series: SeriesBible, usage: Usage | None = None
    ) -> GenerateResponse:
        return GenerateResponse(
            id=bid,
            filename=f"{slugify(series.plan.series_title)}-series.md",
            markdown=render_series(series),
            usage=usage or Usage(),
        )

    @app.post("/api/series", response_model=GenerateResponse)
    def generate_series(
        req: SeriesRequest, request: Request, _user: Any = Depends(require_user)
    ) -> GenerateResponse:
        enforce_quota(request)
        settings = resolve_settings(request, req)
        backend = make_backend(settings)
        t0 = time.monotonic()
        try:
            series = build_series(req.spec, backend, request_stage_models(request, req))
        except BackendError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        bid = library.save_series(
            current_owner(request),
            series,
            backend.usage.total_tokens,
            time.monotonic() - t0,
            settings.model,
        )
        return _series_response(bid, series, backend.usage)

    @app.post("/api/series/stream")
    def generate_series_stream(
        req: SeriesRequest, request: Request, _user: Any = Depends(require_user)
    ) -> StreamingResponse:
        owner = current_owner(request)
        enforce_quota(request)  # before the stream opens, so denial is a normal 429
        settings = resolve_settings(request, req)
        backend = make_backend(settings)
        stage_models = request_stage_models(request, req)

        def events() -> Iterator[str]:
            t0 = time.monotonic()
            try:
                for event in build_series_iter(req.spec, backend, stage_models):
                    if isinstance(event, SeriesBible):
                        bid = library.save_series(
                            owner,
                            event,
                            backend.usage.total_tokens,
                            time.monotonic() - t0,
                            settings.model,
                        )
                        payload = {
                            "done": True,
                            "id": bid,
                            "filename": f"{slugify(event.plan.series_title)}-series.md",
                            "markdown": render_series(event),
                            "usage": backend.usage.model_dump(),
                            "model": settings.model,
                        }
                    else:
                        stage, index, total = event
                        payload = {
                            "stage": stage,
                            "index": index,
                            "total": total,
                            "usage": backend.usage.model_dump(),
                            "model": settings.model,
                        }
                    yield f"data: {json.dumps(payload)}\n\n"
            except BackendError as exc:
                yield f"data: {json.dumps({'error': str(exc)})}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/api/series", response_model=list[BibleSummary])
    def list_series(request: Request, _user: Any = Depends(require_user)) -> list[BibleSummary]:
        return library.list_series(current_owner(request))

    @app.get("/api/series/{bid}", response_model=GenerateResponse)
    def get_series(
        bid: str, request: Request, _user: Any = Depends(require_user)
    ) -> GenerateResponse:
        series = library.get_series(current_owner(request), bid)
        if series is None:
            raise HTTPException(status_code=404, detail="not found")
        return _series_response(bid, series)

    @app.post("/api/series/{bid}/regenerate", response_model=GenerateResponse)
    def regenerate_series_book(
        bid: str, body: RegenerateBookRequest, request: Request, _user: Any = Depends(require_user)
    ) -> GenerateResponse:
        owner = current_owner(request)
        existing = library.get_series(owner, bid)
        if existing is None:
            raise HTTPException(status_code=404, detail="not found")
        if not 1 <= body.book <= len(existing.books):
            raise HTTPException(status_code=400, detail=f"book must be 1..{len(existing.books)}")
        enforce_quota(request)  # after the 404/400 checks, so a bad request doesn't burn a slot
        settings = resolve_settings(request, body)
        backend = make_backend(settings)
        t0 = time.monotonic()
        try:
            updated = regenerate_book(
                backend, existing, body.book, request_stage_models(request, body)
            )
        except BackendError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        library.update_series(
            owner, bid, updated, backend.usage.total_tokens, time.monotonic() - t0, settings.model
        )
        return _series_response(bid, updated, backend.usage)

    @app.get("/api/series/{bid}/export")
    def export_series(
        bid: str,
        request: Request,
        fmt: str = Query("docx", alias="format"),
        _user: Any = Depends(require_user),
    ) -> Response:
        series = library.get_series(current_owner(request), bid)
        if series is None:
            raise HTTPException(status_code=404, detail="not found")
        slug = slugify(series.plan.series_title) + "-series"
        if fmt == "docx":
            data, media, name = series_to_docx(series), DOCX_MEDIA_TYPE, f"{slug}.docx"
        elif fmt == "obsidian":
            data, media, name = series_to_obsidian_zip(series), "application/zip", f"{slug}.zip"
        else:
            raise HTTPException(status_code=400, detail="format must be docx or obsidian")
        return Response(
            content=data,
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.delete("/api/series/{bid}", status_code=204)
    def delete_series(bid: str, request: Request, _user: Any = Depends(require_user)) -> Response:
        if not library.delete(current_owner(request), bid, "series"):
            raise HTTPException(status_code=404, detail="not found")
        return Response(status_code=204)

    app.mount("/", StaticFiles(directory=str(_STATIC), html=True), name="static")
    return app


app = create_app()
