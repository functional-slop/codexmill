"""Unit-level checks on the offline fake backend and schema validation."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from codexmill.config import Settings
from codexmill.llm import BackendError, FakeBackend, OpenAIBackend
from codexmill.schemas import CharacterSet, Outline, Premise


def test_fake_backend_returns_valid_premise() -> None:
    premise = FakeBackend().generate("sys", "user", Premise)
    assert premise.logline
    assert premise.tropes


def test_fake_backend_returns_cast_and_outline() -> None:
    cast = FakeBackend().generate("sys", "user", CharacterSet)
    outline = FakeBackend().generate("sys", "user", Outline)
    assert len(cast.characters) >= 2
    assert [c.number for c in outline.chapters] == [1, 2, 3]


def test_fake_outline_honors_requested_chapter_count() -> None:
    outline = FakeBackend().generate("sys", "Target: 24 chapters, POV third", Outline)
    assert len(outline.chapters) == 24
    assert [c.number for c in outline.chapters[:3]] == [1, 2, 3]
    assert outline.chapters[-1].number == 24


def test_fake_backend_unknown_schema_raises() -> None:
    class Unknown(Premise):
        pass

    with pytest.raises(BackendError):
        FakeBackend().generate("sys", "user", Unknown)


def test_openai_backend_unreachable_endpoint_is_clean_error() -> None:
    # A wrong/unreachable base URL must surface a friendly BackendError, not hang or leak a raw
    # connection traceback. Port 1 refuses immediately — no network egress, no API call, no cost.
    be = OpenAIBackend(
        Settings(
            backend="openai",
            base_url="http://127.0.0.1:1/v1",
            model="nope",
            api_key="x",
            temperature=0.0,
            timeout=2.0,
        )
    )
    with pytest.raises(BackendError) as exc:
        be.generate("sys", "user", Premise)
    assert "AI engine" in str(exc.value)  # user-facing wording, not a raw stack


class _Msg:
    def __init__(self, content: str) -> None:
        self.content = content


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content: str) -> None:
        self.choices = [_Choice(content)]
        self.usage = None


def _backend() -> OpenAIBackend:
    return OpenAIBackend(
        Settings(
            backend="openai",
            base_url="http://127.0.0.1:1/v1",
            model="m",
            api_key="x",
            temperature=0.0,
            timeout=2.0,
        )
    )


_PREMISE = '{"logline":"x","genre":"g","tropes":["b"],"central_conflict":"c","hook":"h"}'


def test_structured_output_requested_when_supported() -> None:
    """A capable endpoint gets a json_schema response_format so chatty/roleplay models can't reply
    in prose that never validates. The named schema is the one being generated."""
    seen: list[dict[str, Any]] = []
    be = _backend()

    def fake_create(**kwargs: Any) -> _Resp:
        seen.append(kwargs)
        return _Resp(_PREMISE)

    be._client.chat.completions.create = fake_create  # type: ignore[assignment]
    be.generate("sys", "user", Premise)
    assert seen[0]["response_format"]["type"] == "json_schema"
    assert seen[0]["response_format"]["json_schema"]["name"] == "Premise"


def test_falls_back_to_prompt_only_when_schema_unsupported() -> None:
    """An endpoint that rejects json_schema (BadRequestError) must not fail the generation — it
    degrades to prompt-only (still validated), and the downgrade is remembered so it isn't retried
    on every later call."""
    from openai import BadRequestError

    be = _backend()
    calls: list[bool] = []  # True = the call carried response_format

    def fake_create(**kwargs: Any) -> _Resp:
        has_fmt = "response_format" in kwargs
        calls.append(has_fmt)
        if has_fmt:
            raise BadRequestError(
                "unsupported response_format",
                response=httpx.Response(400, request=httpx.Request("POST", "http://x/v1")),
                body=None,
            )
        return _Resp(_PREMISE)

    be._client.chat.completions.create = fake_create  # type: ignore[assignment]
    be.generate("sys", "user", Premise)  # succeeds via the fallback
    assert calls == [True, False]  # tried structured once, then prompt-only
    assert be._structured is False
    be.generate("sys", "user", Premise)  # second run skips the structured attempt entirely
    assert calls == [True, False, False]


def _gemini_notfound(model_msg: str) -> Any:
    from openai import NotFoundError

    return NotFoundError(
        "Not Found",
        response=httpx.Response(
            404, request=httpx.Request("POST", "https://generativelanguage.googleapis.com/v1/x")
        ),
        body={"error": {"code": 404, "message": model_msg}},
    )


def test_notfound_surfaces_provider_reason_and_logs_raw_detail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A 404 must report the provider's ACTUAL reason (not a guessed 'tier' cause) to the user, and
    the server log must capture the raw provider response so a failure is diagnosed from ground
    truth. This is the exact case that had to be diagnosed by guesswork before."""
    be = _backend()
    real_reason = "models/gemini-2.5-flash is not found for API version v1beta"

    def fake_create(**kwargs: Any) -> _Resp:
        raise _gemini_notfound(real_reason)

    be._client.chat.completions.create = fake_create  # type: ignore[assignment]
    with caplog.at_level("WARNING", logger="codexmill.llm"), pytest.raises(BackendError) as ei:
        be.generate("sys", "user", Premise)
    # the user-facing message carries the provider's real words, not an invented tier explanation
    assert real_reason in str(ei.value)
    assert "tier" not in str(ei.value).lower()
    # and the raw provider detail (status + body + model) is logged for diagnosis
    logged = caplog.text
    assert "status=404" in logged and real_reason in logged and "model='m'" in logged


def test_logs_and_messages_never_leak_a_key(caplog: pytest.LogCaptureFixture) -> None:
    """Defence in depth: even if a provider echoes a key/token in its error, neither the log line
    nor the user-facing message may contain it. The tokens are SYNTHETIC and assembled at runtime
    (concatenated, never a key-shaped literal) so they exercise the redactor without tripping a
    secret scanner on the public repo."""
    be = _backend()
    fake_google = "AI" + "za" + "Sy" + "FAKE" + "0" * 33  # matches the Google-key pattern, all FAKE
    fake_openai = "s" + "k-" + "FAKE" + "0" * 20  # matches the sk- pattern, obviously not real
    leaky = f"denied: {fake_google} (Bearer {fake_openai})"

    def fake_create(**kwargs: Any) -> _Resp:
        raise _gemini_notfound(leaky)

    be._client.chat.completions.create = fake_create  # type: ignore[assignment]
    with caplog.at_level("WARNING", logger="codexmill.llm"), pytest.raises(BackendError) as ei:
        be.generate("sys", "user", Premise)
    for blob in (caplog.text, str(ei.value)):
        assert fake_google not in blob
        assert fake_openai not in blob
        assert "<redacted>" in blob  # it WAS scrubbed, not just absent by luck
