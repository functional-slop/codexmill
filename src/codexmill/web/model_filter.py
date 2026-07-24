"""Which of a server's models are worth offering for story generation.

A self-hosted Ollama (or any OpenAI-compatible host) usually carries models that cannot write
prose: embedding models, OCR and speech models, rerankers, moderation classifiers, image
generators. Listing those in a user-facing picker is noise at best, and at worst a user picks one
and gets a baffling failure. So the picker shows a curated subset.

This is a deliberate **best-effort name filter**: the OpenAI-compatible ``/v1/models`` endpoint
returns ids only, with no capability metadata, so there is nothing authoritative to test against.
The rule is therefore conservative: exclude only families that are unambiguously not generators,
and keep anything unrecognised, because a false exclude (hiding a model the user wanted) is worse
than a false include (they try it once and switch).
"""

from __future__ import annotations

import os
from collections.abc import Iterable

# Substrings (case-insensitive) of model families that cannot write a story. These are matched
# against a model id, so a cloud provider's ``/models`` list is trimmed to just its chat models —
# no image/video/audio generators, embedders, rerankers, OCR, or moderation classifiers cluttering
# the picker (this is what hides Gemini's "nano banana" image models, Imagen, Veo, DALL·E, etc.).
_NOT_GENERATIVE = (
    "embed",  # nomic-embed-text, mxbai-embed, *-embedding, text-embedding-*
    "bge-",
    "gte-",
    "e5-",
    "minilm",
    "rerank",
    "ocr",  # LightOnOCR and friends
    "whisper",
    "tts",
    "speech",
    "parler",
    "clip",  # clip / siglip encoders
    # image + video generators (output pixels, not prose)
    "stable-diffusion",
    "sdxl",
    "flux",
    "imagen",  # Google Imagen
    "veo",  # Google Veo (video)
    "-image",  # gemini-2.5-flash-image ("nano banana"), gemini-2.0-flash-*-image, *-image-preview
    "dall-e",  # OpenAI DALL·E
    "gpt-image",  # OpenAI gpt-image-1
    "grok-2-image",  # xAI image model
    # classifiers / non-chat utility endpoints
    "guard",  # llama-guard / shieldgemma: moderation classifiers
    "shieldgemma",
    "moderation",  # openai omni/text-moderation
    "aqa",  # Google Attributed-QA endpoint, not a chat model
)


# Above this on-disk size, warn that generation will be slow. Size is a rough proxy: a big *dense*
# model is slow, while a similarly-sized mixture-of-experts can be fast because only a fraction of
# its weights run per token. The threshold is set so typical MoE/mid-size models stay unflagged and
# only genuinely heavy models are called out. Nothing is blocked — the user is just told.
LARGE_MODEL_BYTES = 30 * 1024**3


def is_large(size_bytes: int | None) -> bool:
    """Whether a model is big enough that a full multi-stage generation will be noticeably slow."""
    return bool(size_bytes and size_bytes >= LARGE_MODEL_BYTES)


def human_size(size_bytes: int | None) -> str:
    """e.g. 48 GB. Empty when the host didn't report a size."""
    if not size_bytes:
        return ""
    gb = size_bytes / 1024**3
    return f"{gb:.0f} GB" if gb >= 1 else f"{size_bytes / 1024**2:.0f} MB"


def _operator_denylist() -> tuple[str, ...]:
    """Instance-specific substrings the operator wants hidden from the picker, from
    ``CODEXMILL_MODEL_DENYLIST`` (comma-separated, case-insensitive). Use it to drop a model that
    technically generates but produces poor output on this pipeline (e.g. a base/roleplay model that
    ignores the injected premise), without touching code."""
    raw = os.environ.get("CODEXMILL_MODEL_DENYLIST", "")
    return tuple(s.strip().lower() for s in raw.split(",") if s.strip())


def is_story_capable(model_id: str) -> bool:
    """False for models that clearly can't write prose (embeddings, OCR, speech, moderation…) or
    that the operator has denylisted for this instance."""
    name = model_id.lower()
    if any(bad in name for bad in _NOT_GENERATIVE):
        return False
    return not any(bad in name for bad in _operator_denylist())


def normalize_model_id(model_id: str) -> str:
    """Normalise a model id from a provider's ``/models`` list to the form its chat endpoint takes.

    Gemini's OpenAI-compatible ``/models`` returns ids namespaced as ``models/gemini-2.5-flash``
    (its native REST convention), but chat/completions wants the bare ``gemini-2.5-flash`` — the
    namespaced form 404s. No other allow-listed provider uses a ``models/`` prefix, so stripping it
    is a safe no-op elsewhere. (This is a listing hygiene fix, NOT the cure for a key that simply
    isn't granted a model: that 404 is the account's tier/billing, surfaced to the user as a clear
    error.)
    """
    return model_id[len("models/") :] if model_id.startswith("models/") else model_id


def filter_models(ids: Iterable[str]) -> list[str]:
    """The subset worth offering, order preserved and duplicates dropped. Ids are normalised to the
    form the chat endpoint accepts (see ``normalize_model_id``)."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in ids:
        mid = normalize_model_id(raw) if raw else raw
        if mid and mid not in seen and is_story_capable(mid):
            seen.add(mid)
            out.append(mid)
    return out


def display_name(model_id: str) -> str:
    """A readable label for a picker. Local model ids are often a whole HuggingFace repo path
    (``hf.co/TheDrummer/Anubis-70B-v1.2-GGUF:Q4_K_M``); show the model, not the plumbing. The value
    submitted back is always the untouched id."""
    name = model_id.rsplit("/", 1)[-1]  # drop hf.co/<org>/
    quant = ""
    if ":" in name:
        name, quant = name.rsplit(":", 1)
        if quant.lower() in ("latest", ""):
            quant = ""
    if name.upper().endswith("-GGUF"):
        name = name[: -len("-GGUF")]
    return f"{name} ({quant})" if quant else name
