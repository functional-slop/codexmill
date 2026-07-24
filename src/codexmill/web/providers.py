"""Cloud frontier providers a non-admin may bring their own key for (ADR 0027).

This is a fixed, server-side allow-list. A user's bring-your-own config is *provider + key + model*:
the ``base_url`` is looked up here and never supplied by the client, so a user cannot point the
server at an arbitrary endpoint. That is what keeps per-user BYO free of SSRF surface — the only
URLs the server will talk to on a user's behalf are these public provider endpoints.

Admins are not restricted to this list: they configure the shared/server AI (including a local
Ollama at an arbitrary ``base_url``) through the admin Settings, and are already trusted to set the
instance config outright.

All endpoints are OpenAI-compatible. ``default_model`` is only a suggestion the UI pre-fills; the
user can type any model the provider offers. To offer another provider, add a row here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Provider:
    name: str  # stable key stored in User.llm["provider"]
    label: str  # human name for the UI
    base_url: str  # OpenAI-compatible endpoint (server-fixed; never from the client)
    default_model: str  # UI pre-fill; user may change it
    key_url: str  # where the user creates an API key
    # Common models to offer as a dropdown. NOT exhaustive and NOT a guarantee a given key can use
    # them (access/tier/billing vary), which is why the model field stays free-text-editable and the
    # "Test" button does a real generation. Order = rough default preference.
    models: tuple[str, ...] = ()


# Widely-used public frontier providers. OpenRouter fronts many additional models, so it covers the
# long tail without widening the URL allow-list.
_PROVIDERS: dict[str, Provider] = {
    "openai": Provider(
        "openai",
        "OpenAI",
        "https://api.openai.com/v1",
        "gpt-4o-mini",
        "https://platform.openai.com/api-keys",
        ("gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1"),
    ),
    "anthropic": Provider(
        "anthropic",
        "Anthropic (Claude)",
        "https://api.anthropic.com/v1",
        "claude-3-5-haiku-latest",
        "https://console.anthropic.com/settings/keys",
        ("claude-3-5-haiku-latest", "claude-3-5-sonnet-latest"),
    ),
    "gemini": Provider(
        "gemini",
        "Google Gemini",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "gemini-2.0-flash",
        "https://aistudio.google.com/apikey",
        ("gemini-2.0-flash", "gemini-2.5-flash", "gemini-1.5-flash", "gemini-1.5-pro"),
    ),
    "groq": Provider(
        "groq",
        "Groq",
        "https://api.groq.com/openai/v1",
        "llama-3.3-70b-versatile",
        "https://console.groq.com/keys",
        ("llama-3.3-70b-versatile", "llama-3.1-8b-instant"),
    ),
    "mistral": Provider(
        "mistral",
        "Mistral",
        "https://api.mistral.ai/v1",
        "mistral-small-latest",
        "https://console.mistral.ai/api-keys",
        ("mistral-small-latest", "mistral-large-latest"),
    ),
    "openrouter": Provider(
        "openrouter",
        "OpenRouter",
        "https://openrouter.ai/api/v1",
        "",
        "https://openrouter.ai/keys",
        (),  # thousands of models; the user types the one they want
    ),
    "xai": Provider(
        "xai",
        "xAI (Grok)",
        "https://api.x.ai/v1",
        "",
        "https://console.x.ai",
        ("grok-2-latest", "grok-beta"),
    ),
}


def get_provider(name: str) -> Provider | None:
    """The allow-listed provider for ``name``, or None if it is not on the list."""
    return _PROVIDERS.get(name)


def is_allowed(name: str) -> bool:
    return name in _PROVIDERS


def catalog() -> list[dict[str, Any]]:
    """The allow-list as plain dicts, for the ``/api/providers`` endpoint the UI renders."""
    return [asdict(p) for p in _PROVIDERS.values()]
