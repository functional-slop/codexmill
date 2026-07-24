"""Curation of a server's model list (web/model_filter).

A self-hosted Ollama carries models that can't write prose. The picker must hide those without
hiding legitimate story models, including HuggingFace-style ids.
"""

from __future__ import annotations

import pytest

from codexmill.web.model_filter import (
    display_name,
    filter_models,
    human_size,
    is_large,
    is_story_capable,
    normalize_model_id,
)

# The real model list from a self-hosted box, which is where this requirement came from.
REAL_HOST = [
    "gemma4:e4b",
    "hf.co/TheDrummer/Anubis-70B-v1.2-GGUF:Q4_K_M",
    "hf.co/TheDrummer/Cydonia-24B-v4.3-GGUF:Q5_K_M",
    "hf.co/noctrex/LightOnOCR-2-1B-GGUF:Q8_0",
    "hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M",
    "nomic-embed-text:latest",
    "qwen3.6-tools:latest",
]


def test_hides_non_generative_models_but_keeps_story_models() -> None:
    kept = filter_models(REAL_HOST)
    # the two that can't write prose are gone
    assert "nomic-embed-text:latest" not in kept
    assert "hf.co/noctrex/LightOnOCR-2-1B-GGUF:Q8_0" not in kept
    # everything that can write is still offered, including the creative-writing finetunes
    assert kept == [
        "gemma4:e4b",
        "hf.co/TheDrummer/Anubis-70B-v1.2-GGUF:Q4_K_M",
        "hf.co/TheDrummer/Cydonia-24B-v4.3-GGUF:Q5_K_M",
        "hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M",
        "qwen3.6-tools:latest",
    ]


def test_excludes_other_non_generative_families() -> None:
    for bad in (
        "mxbai-embed-large",
        "bge-m3",
        "snowflake-arctic-embed2",
        "whisper-large",
        "llama-guard3:8b",
        "shieldgemma:9b",
        "some-reranker:v1",
        "stable-diffusion-xl",
    ):
        assert is_story_capable(bad) is False, bad


def test_hides_image_and_video_and_utility_models() -> None:
    """Cloud providers list image/video/moderation endpoints that can't write prose; trim them so
    the picker shows only chat models (this is what removes Gemini's 'nano banana' image models)."""
    for bad in (
        "gemini-2.5-flash-image",  # "nano banana"
        "gemini-2.5-flash-image-preview",
        "gemini-2.0-flash-preview-image-generation",
        "imagen-3.0-generate-002",
        "veo-2.0-generate-001",
        "dall-e-3",
        "gpt-image-1",
        "grok-2-image-1212",
        "omni-moderation-latest",
        "text-moderation-latest",
        "aqa",
    ):
        assert is_story_capable(bad) is False, bad


def test_keeps_real_chat_models_from_every_provider() -> None:
    """The trim must NOT catch a real chat model on any allow-listed provider."""
    for good in (
        "gemini-2.0-flash",
        "gemini-2.5-flash",
        "gemini-2.5-pro",
        "gemini-1.5-pro",
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4.1",
        "claude-3-5-sonnet-latest",
        "llama-3.3-70b-versatile",
        "mistral-large-latest",
        "grok-2-latest",
    ):
        assert is_story_capable(good) is True, good


def test_keeps_unrecognised_models() -> None:
    """Conservative on purpose: hiding a model the user wanted is worse than showing one extra."""
    for good in ("llama3.3:70b", "mistral-small:latest", "some-new-model:v2", "phi4:14b"):
        assert is_story_capable(good) is True, good


def test_filter_preserves_order_and_dedupes() -> None:
    assert filter_models(["a:1", "b:1", "a:1", ""]) == ["a:1", "b:1"]


def test_normalize_strips_only_a_models_prefix() -> None:
    # Gemini's /models lists "models/gemini-2.5-flash" but chat wants the bare id.
    assert normalize_model_id("models/gemini-2.5-flash") == "gemini-2.5-flash"
    # no prefix -> unchanged (safe no-op for every other provider)
    assert normalize_model_id("gemini-2.0-flash") == "gemini-2.0-flash"
    assert normalize_model_id("gpt-4o-mini") == "gpt-4o-mini"
    # only a leading occurrence is stripped, and never mid-string
    assert normalize_model_id("openai/models/foo") == "openai/models/foo"


def test_filter_normalises_gemini_namespaced_ids() -> None:
    # a namespaced Gemini list comes back as bare ids the chat endpoint accepts, still deduped
    assert filter_models(["models/gemini-2.5-flash", "gemini-2.5-flash"]) == ["gemini-2.5-flash"]


def test_large_model_warning_flags_the_heavy_one_only() -> None:
    """Real sizes from the host this came from: the 70B is flagged slow, the everyday models are
    not. Size is a rough proxy (a big MoE can still be fast), so the threshold is set to catch
    genuinely heavy models rather than everything mid-size."""
    gb = 1024**3
    assert is_large(48 * gb) is True  # Anubis-70B — the one that timed out
    assert is_large(22 * gb) is False  # qwen3.6-tools (MoE, fast despite its size)
    assert is_large(10 * gb) is False  # gemma4:e4b
    assert is_large(None) is False  # host reported no size -> no warning, never a false alarm
    assert is_large(0) is False


def test_human_size_reads_naturally() -> None:
    gb = 1024**3
    assert human_size(48 * gb) == "48 GB"
    assert human_size(None) == ""
    assert human_size(0) == ""


def test_display_name_is_readable() -> None:
    assert (
        display_name("hf.co/TheDrummer/Anubis-70B-v1.2-GGUF:Q4_K_M") == "Anubis-70B-v1.2 (Q4_K_M)"
    )
    assert display_name("qwen3.6-tools:latest") == "qwen3.6-tools"  # ":latest" is noise
    assert display_name("gemma4:e4b") == "gemma4 (e4b)"


def test_operator_denylist_hides_named_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """An operator can hide a model that technically generates but produces poor output on this
    pipeline (e.g. a base model that ignores the injected premise), via CODEXMILL_MODEL_DENYLIST."""
    monkeypatch.setenv("CODEXMILL_MODEL_DENYLIST", "qwen3.6-35b-a3b, cydonia")
    kept = filter_models(
        [
            "gemma4:e4b",
            "hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M",
            "hf.co/TheDrummer/Cydonia-24B-v4.3-GGUF:Q5_K_M",
            "qwen3.6-tools:latest",
        ]
    )
    assert kept == ["gemma4:e4b", "qwen3.6-tools:latest"]  # both denylisted families gone
    monkeypatch.delenv("CODEXMILL_MODEL_DENYLIST")
    assert "hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M" in filter_models(
        ["hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M"]
    )  # gone from env -> visible again
