"""FastAPI app (factory). The browser talks only to this server; the server talks to the LLM.
Keys in the generate form are used per-request and never stored.

Auth is MANDATORY (ADR 0024). A fresh instance is CLOSED: only `/api/me` and `/api/auth/setup` work
until a local admin account (username + argon2id password) is created in first-run onboarding. After
that every route needs a session. There is no open bootstrap.

OIDC (ADR 0008/0009) remains optional on top: SSO for a shared instance, configured in the /admin
GUI or via env, toggleable at runtime. A session from either source authenticates. Admin = the local
account, an OIDC user on the email allowlist, or the CODEXMILL_SETUP_TOKEN break-glass."""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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
from codexmill.web.store import ConfigStore

_STATIC = Path(__file__).parent / "static"


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


class RegenerateRequest(_LLMOverrides):
    stage: str  # one of pipeline.STAGE_LABELS


class OIDCSettings(BaseModel):
    issuer: str = ""
    client_id: str = ""
    client_secret: str = ""  # write-only; blank keeps the stored one
    admin_emails: list[str] = []


class OIDCStatus(BaseModel):
    enabled: bool
    issuer: str
    client_id: str
    has_secret: bool
    admin_emails: list[str]
    source: str  # store | env | none


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


def create_app(store: ConfigStore | None = None, library: Library | None = None) -> FastAPI:
    store = store or ConfigStore()
    library = library or Library()
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

    def effective_settings(req: _LLMOverrides) -> Settings:
        """Precedence for LLM config: per-request override > saved server default > env."""
        s = store.get_llm()
        return Settings.from_overrides(
            backend=req.backend or (str(s["backend"]) if s.get("backend") else None),
            base_url=req.base_url or (str(s["base_url"]) if s.get("base_url") else None),
            model=req.model or (str(s["model"]) if s.get("model") else None),
            api_key=req.api_key or (str(s["api_key"]) if s.get("api_key") else None),
        )

    def request_stage_models(req: _LLMOverrides) -> dict[str, str]:
        # A request-level model overrides all stages uniformly; else use stored per-stage models.
        return {} if req.model else _stage_models(store.get_llm())

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

    def enforce_quota(owner: str) -> None:
        """Consume one generation slot for ``owner``; raise 429 if the quota is exhausted. A no-op
        when no quota is configured (the default). See ADR 0022."""
        max_gen, window, _ = effective_rate_limit()
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
        return not store.has_users() and current_oidc() is None

    def _identity(user: dict[str, Any]) -> str:
        """The revocation key for a session: username (local) or email/sub (OIDC)."""
        if user.get("kind") == "local":
            return str(user.get("username") or "")
        return str(user.get("email") or user.get("sub") or "")

    def session_user(request: Request) -> dict[str, Any] | None:
        """The authenticated session, from the local account or OIDC (`kind` distinguishes). A
        session whose epoch no longer matches the store (logout / password change elsewhere) is
        rejected, so a signed-but-revoked cookie can't be replayed."""
        user = request.session.get("user")
        if not (isinstance(user, dict) and user):
            return None
        if user.get("epoch") != store.current_session_epoch(_identity(user)):
            return None  # revoked / stale cookie
        return user

    def _sign_in(request: Request, user: dict[str, Any]) -> dict[str, Any]:
        """Stamp the session with the identity's current epoch so it can later be revoked."""
        user["epoch"] = store.ensure_session_epoch(_identity(user))
        request.session["user"] = user
        return user

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
        """Whose library a request reads/writes. Local accounts own by username; OIDC users by the
        id_token `sub` (falling back to a verified email, then `anonymous`)."""
        user = session_user(request) or {}
        if user.get("kind") == "local":
            return str(user.get("username") or "local")
        if user:
            # Prefer the stable `sub` (always present in a conformant id_token) so OIDC users with
            # no/unverified email don't collapse into one shared "anonymous" bucket. `email` is only
            # present here when the IdP marked it verified (see auth_callback).
            return str(user.get("sub") or user.get("email") or "anonymous")
        return "local"

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
        if user.get("kind") == "local":
            return  # the local account IS the admin (single-account model)
        # OIDC user: require an explicitly-allowlisted admin. Fail CLOSED if no admins are
        # configured — an empty allowlist must NOT mean "every logged-in user is admin".
        emails = effective_admin_emails()
        if emails and str(user.get("email", "")) in emails:
            return
        raise HTTPException(status_code=403, detail="admin access required")

    @app.get("/auth/login")
    async def login(request: Request) -> Any:
        oidc = current_oidc()
        if oidc is None:
            raise HTTPException(status_code=404, detail="OIDC is not configured")
        return await oauth_for(oidc).oidc.authorize_redirect(
            request, request.url_for("auth_callback")
        )

    @app.get("/auth/callback", name="auth_callback")
    async def auth_callback(request: Request) -> RedirectResponse:
        oidc = current_oidc()
        if oidc is None:
            raise HTTPException(status_code=404, detail="OIDC is not configured")
        token = await oauth_for(oidc).oidc.authorize_access_token(request)
        info = dict(token.get("userinfo") or {})
        info["kind"] = "oidc"
        # Do NOT trust an unverified email: it gates admin access (require_admin) and used to key
        # library ownership. An IdP that lets a user self-set an arbitrary/unverified email could
        # otherwise claim an admin's or a victim's email. Drop it unless explicitly verified.
        ev = info.get("email_verified")
        if info.get("email") and not (ev is True or (isinstance(ev, str) and ev.lower() == "true")):
            info.pop("email", None)
        _sign_in(request, info)
        return RedirectResponse(url="/")

    def _revoke_session(request: Request) -> None:
        user = request.session.get("user")
        if isinstance(user, dict) and user:
            store.rotate_session_epoch(_identity(user))  # kill this identity's cookies everywhere
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
        store.create_user(username, body.password)
        _sign_in(request, {"kind": "local", "username": username})
        return {"ok": True, "username": username}

    @app.post("/api/auth/login")
    def auth_login(body: LoginRequest, request: Request) -> dict[str, Any]:
        who = request.client.host if request.client else "unknown"
        login_throttle.check(who)  # 429 while locked out from repeated failures
        if not store.verify_user(body.username.strip(), body.password):
            login_throttle.record_failure(who)
            # Same message either way — never reveal whether the username exists. (verify_user also
            # burns a dummy hash on unknown users so timing doesn't leak it.)
            raise HTTPException(status_code=401, detail="incorrect username or password")
        login_throttle.reset(who)
        _sign_in(request, {"kind": "local", "username": body.username.strip()})
        return {"ok": True, "username": body.username.strip()}

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
        return {
            "oidc_enabled": enabled,
            # first run: no local account and no OIDC -> the UI must create an admin first
            "needs_setup": needs_setup(),
            # first-run setup requires the break-glass token when the operator set one (guards a
            # LAN-exposed instance against a setup land-grab before the owner finishes onboarding)
            "setup_requires_token": needs_setup() and bool(os.environ.get("CODEXMILL_SETUP_TOKEN")),
            "authenticated": bool(user),
            "username": (user or {}).get("username") or (user or {}).get("email") or "",
            "user": user,
            "has_llm_default": bool(store.get_llm()),
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
        import httpx

        current = store.get_llm()
        stored_base = str(current.get("base_url", ""))
        base = (body.base_url or stored_base).rstrip("/")
        # NEVER send the stored API key to a base_url the caller just typed in — otherwise "test
        # connection" against an attacker URL exfiltrates the saved provider key. Only reuse the
        # stored key when testing the stored base_url; a new URL must bring its own key.
        if body.base_url and body.base_url.rstrip("/") != stored_base.rstrip("/"):
            key = body.api_key
        else:
            key = body.api_key or str(current.get("api_key", ""))
        if not base:
            return {"ok": False, "error": "no base URL set"}
        try:
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            r = httpx.get(base + "/models", headers=headers, timeout=8.0)
            r.raise_for_status()
            models = [m.get("id") for m in r.json().get("data", [])][:25]
            return {"ok": True, "models": models}
        except Exception as exc:  # report any failure back to the admin UI
            return {"ok": False, "error": str(exc)[:200]}

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

    @app.post("/api/surprise", response_model=StorySeed)
    def surprise(
        req: _LLMOverrides, request: Request, _user: Any = Depends(require_user)
    ) -> StorySeed:
        enforce_quota(current_owner(request))  # it makes a real LLM call; count it toward the quota
        backend = make_backend(effective_settings(req))
        try:
            return surprise_stage.generate(backend)
        except BackendError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/generate", response_model=GenerateResponse)
    def generate(
        req: GenerateRequest, request: Request, _user: Any = Depends(require_user)
    ) -> GenerateResponse:
        enforce_quota(current_owner(request))
        settings = effective_settings(req)
        backend = make_backend(settings)
        t0 = time.monotonic()
        try:
            bible = build(req.spec, backend, request_stage_models(req))
        except BackendError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        bid = library.save(
            current_owner(request), bible, backend.usage.total_tokens, time.monotonic() - t0
        )
        return GenerateResponse(
            id=bid,
            filename=f"{slugify(bible.premise.logline)}.md",
            markdown=render_bible(bible),
            usage=backend.usage,
        )

    @app.post("/api/generate/stream")
    def generate_stream(
        req: GenerateRequest, request: Request, _user: Any = Depends(require_user)
    ) -> StreamingResponse:
        owner = current_owner(request)
        enforce_quota(owner)  # before the stream opens, so denial is a normal 429
        settings = effective_settings(req)
        backend = make_backend(settings)

        stage_models = request_stage_models(req)

        def events() -> Iterator[str]:
            t0 = time.monotonic()
            try:
                for event in build_iter(req.spec, backend, stage_models):
                    if isinstance(event, StoryBible):
                        bid = library.save(
                            owner, event, backend.usage.total_tokens, time.monotonic() - t0
                        )
                        payload = {
                            "done": True,
                            "id": bid,
                            "filename": f"{slugify(event.premise.logline)}.md",
                            "markdown": render_bible(event),
                            "usage": backend.usage.model_dump(),
                        }
                    else:
                        stage, index, total = event
                        # usage so far (stages completed before this one) drives a live token count
                        payload = {
                            "stage": stage,
                            "index": index,
                            "total": total,
                            "usage": backend.usage.model_dump(),
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
            id=bid, filename=f"{slugify(bible.premise.logline)}.md", markdown=render_bible(bible)
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
        enforce_quota(owner)  # after the 404/400 checks, so a bad request doesn't burn a slot
        backend = make_backend(effective_settings(body))
        t0 = time.monotonic()
        try:
            updated = regenerate(backend, existing, body.stage, request_stage_models(body))
        except BackendError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        library.update(owner, bid, updated, backend.usage.total_tokens, time.monotonic() - t0)
        return GenerateResponse(
            id=bid,
            filename=f"{slugify(updated.premise.logline)}.md",
            markdown=render_bible(updated),
            usage=backend.usage,
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
        enforce_quota(current_owner(request))
        backend = make_backend(effective_settings(req))
        t0 = time.monotonic()
        try:
            series = build_series(req.spec, backend, request_stage_models(req))
        except BackendError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        bid = library.save_series(
            current_owner(request), series, backend.usage.total_tokens, time.monotonic() - t0
        )
        return _series_response(bid, series, backend.usage)

    @app.post("/api/series/stream")
    def generate_series_stream(
        req: SeriesRequest, request: Request, _user: Any = Depends(require_user)
    ) -> StreamingResponse:
        owner = current_owner(request)
        enforce_quota(owner)  # before the stream opens, so denial is a normal 429
        backend = make_backend(effective_settings(req))
        stage_models = request_stage_models(req)

        def events() -> Iterator[str]:
            t0 = time.monotonic()
            try:
                for event in build_series_iter(req.spec, backend, stage_models):
                    if isinstance(event, SeriesBible):
                        bid = library.save_series(
                            owner, event, backend.usage.total_tokens, time.monotonic() - t0
                        )
                        payload = {
                            "done": True,
                            "id": bid,
                            "filename": f"{slugify(event.plan.series_title)}-series.md",
                            "markdown": render_series(event),
                            "usage": backend.usage.model_dump(),
                        }
                    else:
                        stage, index, total = event
                        payload = {
                            "stage": stage,
                            "index": index,
                            "total": total,
                            "usage": backend.usage.model_dump(),
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
        enforce_quota(owner)  # after the 404/400 checks, so a bad request doesn't burn a slot
        backend = make_backend(effective_settings(body))
        t0 = time.monotonic()
        try:
            updated = regenerate_book(backend, existing, body.book, request_stage_models(body))
        except BackendError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        library.update_series(
            owner, bid, updated, backend.usage.total_tokens, time.monotonic() - t0
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
