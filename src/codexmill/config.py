"""Backend-agnostic configuration. Everything comes from the environment; no provider,
model, or URL is hardcoded in stage logic. See docs/adr/0003."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_BASE_URL = "http://localhost:11434/v1"  # local Ollama, OpenAI-compatible
DEFAULT_MODEL = "gemma4:e4b"


@dataclass(frozen=True)
class Settings:
    backend: str
    base_url: str
    model: str
    api_key: str
    temperature: float
    timeout: float = 120.0  # per-LLM-call timeout in seconds; guards against a hung endpoint

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            backend=os.environ.get("CODEXMILL_BACKEND", "openai"),
            base_url=os.environ.get("CODEXMILL_BASE_URL", DEFAULT_BASE_URL),
            model=os.environ.get("CODEXMILL_MODEL", DEFAULT_MODEL),
            api_key=os.environ.get("CODEXMILL_API_KEY", "ollama"),
            temperature=float(os.environ.get("CODEXMILL_TEMPERATURE", "0.8")),
            timeout=float(os.environ.get("CODEXMILL_TIMEOUT", "120")),
        )

    @classmethod
    def from_overrides(
        cls,
        *,
        backend: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ) -> Settings:
        """Start from env defaults, then apply any explicit (non-None) overrides. Used by the web
        UI, where a writer supplies backend/model/key in the form rather than the environment."""
        base = cls.from_env()
        return cls(
            backend=backend or base.backend,
            base_url=base_url or base.base_url,
            model=model or base.model,
            api_key=api_key or base.api_key,
            temperature=base.temperature,
            timeout=base.timeout,
        )
