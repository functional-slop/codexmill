"""The pluggable LLM layer. `Backend` is the protocol every stage talks to. `OpenAIBackend`
targets any OpenAI-compatible endpoint; `FakeBackend` is deterministic and offline for tests
and demos. Structured output is enforced app-side (prompt for JSON -> validate -> retry) so it
stays portable across providers. See docs/adr/0003."""

from __future__ import annotations

import json
import re
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from codexmill.config import Settings

T = TypeVar("T", bound=BaseModel)


class BackendError(RuntimeError):
    """The backend could not produce a valid result."""


class Usage(BaseModel):
    """Token tally for a run, accumulated across every LLM call a backend makes (ADR 0021).
    Tokens are what providers bill on; we report them and let the user apply their own rate."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    calls: int = 0  # billed round-trips (each JSON-repair retry counts — it costs money too)

    def add(self, prompt: int, completion: int, total: int) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += total
        self.calls += 1


class Backend(Protocol):
    usage: Usage  # per-instance tally, read after a run to meter its cost (ADR 0021)

    def generate(self, system: str, user: str, schema: type[T], model: str | None = None) -> T:
        """Return an instance of `schema`, validated. `model` optionally overrides the backend's
        default model for this call (per-stage model selection)."""
        ...


def _extract_json(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        parts = t.split("```")
        if len(parts) >= 2:
            t = parts[1]
            if t.startswith("json"):
                t = t[4:]
            t = t.strip()
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end > start:
        return t[start : end + 1]
    return t


def _parse_payload(text: str, schema: type[BaseModel]) -> Any:
    """Parse model output into a dict ready for validation, tolerating a common failure of weaker
    models: wrapping the real object under a single schema-named key,
    e.g. ``{"ChapterExpansion": {...}}`` instead of ``{...}``. Only unwrap when the top-level keys
    do not overlap the schema's fields, so a legitimately-shaped object is never mangled."""
    data = json.loads(_extract_json(text))
    if isinstance(data, dict) and len(data) == 1 and not (set(schema.model_fields) & data.keys()):
        inner = next(iter(data.values()))
        if isinstance(inner, dict):
            return inner
    return data


def _friendly_openai_error(exc: Exception) -> str:
    """Turn an openai SDK error into a message a non-technical user can act on."""
    from openai import (
        APIConnectionError,
        APITimeoutError,
        AuthenticationError,
        NotFoundError,
        RateLimitError,
    )

    if isinstance(exc, APITimeoutError):
        return "the AI engine took too long to respond (timed out). Try again or a faster model."
    if isinstance(exc, AuthenticationError):
        return "the AI engine rejected the API key. Check your key in Settings."
    if isinstance(exc, NotFoundError):
        return "the AI engine could not find that model. Check the model name in Settings."
    if isinstance(exc, RateLimitError):
        return "the AI engine is rate-limiting or out of quota. Wait a moment and retry."
    if isinstance(exc, APIConnectionError):
        return "could not reach the AI engine. Check the base URL and that the engine is running."
    return f"the AI engine returned an error: {str(exc)[:200]}"


class OpenAIBackend:
    """Any OpenAI-compatible `/v1` endpoint (Ollama, Gemini, Groq, OpenRouter, ...)."""

    def __init__(self, settings: Settings) -> None:
        from openai import OpenAI

        # A per-call timeout + a single SDK-level retry so a hung or unreachable endpoint fails
        # fast with a clean error instead of blocking a request (and its worker thread) forever.
        self._client = OpenAI(
            base_url=settings.base_url,
            api_key=settings.api_key,
            timeout=settings.timeout,
            max_retries=1,
        )
        self._model = settings.model
        self._temperature = settings.temperature
        self.usage = Usage()

    def generate(self, system: str, user: str, schema: type[T], model: str | None = None) -> T:
        from openai import OpenAIError

        schema_json = json.dumps(schema.model_json_schema())
        prompt = (
            f"{user}\n\nReturn ONLY valid JSON matching this JSON Schema. "
            f"No prose, no code fences.\n\nSchema:\n{schema_json}"
        )
        last_err: Exception | None = None
        for _ in range(3):
            try:
                resp = self._client.chat.completions.create(
                    model=model or self._model,
                    temperature=self._temperature,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                )
            except OpenAIError as exc:  # timeout, connection, auth, rate-limit, bad model, ...
                raise BackendError(_friendly_openai_error(exc)) from exc
            u = getattr(resp, "usage", None)  # some OpenAI-compatible servers omit usage
            self.usage.add(
                getattr(u, "prompt_tokens", 0) or 0,
                getattr(u, "completion_tokens", 0) or 0,
                getattr(u, "total_tokens", 0) or 0,
            )
            # Some OpenAI-compatible servers return an empty `choices` list on an error condition;
            # turn that into a clean BackendError instead of an IndexError -> 500.
            if not resp.choices:
                raise BackendError("the AI engine returned an empty response. Try again.")
            content = resp.choices[0].message.content or ""
            try:
                return schema.model_validate(_parse_payload(content, schema))
            except (ValidationError, ValueError) as exc:
                last_err = exc
                prompt = (
                    f"{prompt}\n\nYour previous reply was not valid: {exc}. "
                    "Return ONLY the bare JSON object with those exact top-level fields — do not "
                    "wrap it under any key."
                )
        raise BackendError(
            f"the AI engine did not return valid {schema.__name__} data after 3 tries: {last_err}"
        )


# Deterministic canned output keyed by schema name — offline tests/demos only.
_FAKE: dict[str, dict[str, Any]] = {
    "StorySeed": {
        "genre": "cozy mystery",
        "premise_hint": "A retired lighthouse keeper who solves crimes by reading the tides "
        "investigates a disappearance the town insists never happened.",
        "tropes": ["amateur sleuth", "small-town secrets", "unreliable memory"],
    },
    "Premise": {
        "logline": "A burned-out pastry chef inherits her aunt's failing seaside bakery and "
        "must save it before the town festival, aided by the gruff harbormaster she can't stand.",
        "genre": "cozy romance",
        "tropes": ["small-town", "grumpy-sunshine", "second-chance"],
        "central_conflict": "Reviving the bakery pits her big-city instincts against the town's "
        "traditions, and her guardedness against the harbormaster's blunt loyalty.",
        "hook": "She can pipe a perfect rose but can't patch a leaking roof — or her own heart.",
    },
    "Worldbuilding": {
        "history": "Brindlemouth grew from a fishing village into a faded resort "
        "town, its heyday a generation past.",
        "geography": "A crescent harbor, a cliffside main street, and the bakery at "
        "the boardwalk's quiet end.",
        "cultures": "Townsfolk who value tradition, long memory, and showing up for one another.",
        "factions": "The old-guard festival committee versus newcomers trying to "
        "modernize the waterfront.",
        "systems": "No magic here; the rules are weather, tides, and reputation, and "
        "all three are unforgiving.",
    },
    "CharacterSet": {
        "characters": [
            {
                "name": "Marisol Vega",
                "role": "protagonist",
                "motivation": "Prove she can build something lasting after her restaurant failed.",
                "flaw": "Treats vulnerability as weakness; controls instead of trusting.",
                "arc": "Learns that leaning on others is not the same as losing herself.",
                "voice": "Crisp, wry, clipped under stress; softens into warmth around food.",
            },
            {
                "name": "Cormac Doyle",
                "role": "supporting",
                "motivation": "Keep the harbor and the town he rebuilt from drifting apart.",
                "flaw": "Confuses stoicism with strength; says too little, too late.",
                "arc": "Learns to speak the thing before the moment to say it has passed.",
                "voice": "Short declaratives, dry humor, weather metaphors, rarely a wasted word.",
            },
        ]
    },
    "Outline": {
        "chapters": [
            {
                "number": 1,
                "title": "Low Tide",
                "beat": "Setup / ordinary world",
                "summary": "Marisol arrives to a shuttered bakery and a town that remembers her "
                "aunt fondly and her not at all; first sparks with Cormac at the harbor.",
            },
            {
                "number": 2,
                "title": "Proof",
                "beat": "Inciting incident",
                "summary": "The festival committee gives her one week to reopen or forfeit the "
                "stall; she and Cormac strike a reluctant bargain over repairs.",
            },
            {
                "number": 3,
                "title": "Rising",
                "beat": "First turning point",
                "summary": "A ruined oven and a shared midnight fix crack both their defenses; the "
                "town starts to root for her, and so, quietly, does he.",
            },
        ]
    },
    "ChapterExpansion": {
        "summary": "Marisol opens the shutters on a bakery that smells of dust and old sugar. She "
        "measures the room the way she measures flour — exactly — and Cormac watches from the "
        "doorway, saying less than he means.",
        "scene_beats": [
            "Marisol takes stock of the ruined bakery and her own doubts.",
            "Cormac offers blunt, unasked-for help; she bristles, then relents.",
            "A small shared success plants the first seed of trust.",
        ],
        "recap": "Marisol and Cormac move one wary notch closer and the bakery survives the day.",
    },
    "KDPMetadata": {
        "keywords": [
            "small town romance",
            "grumpy sunshine romance",
            "second chance romance",
            "seaside small town",
            "bakery romance",
            "slow burn romance",
            "found family romance",
        ],
        "categories": [
            "Romance > Contemporary",
            "Romance > Holidays",
            "Fiction > Small Town & Rural",
        ],
        "blurb": "Marisol Vega left the city with a chef's-knife reputation and nothing else. Her "
        "aunt's seaside bakery was supposed to be a quiet place to lick her wounds — until she "
        "finds it failing, the roof leaking, and the town's gruff harbormaster convinced she'll "
        "bolt by autumn. Cormac Doyle says little and expects less, but every midnight repair and "
        "burnt first batch pulls them closer to a truth neither wants to name. With the summer "
        "festival looming and the bakery's fate on the line, Marisol must decide whether a "
        "perfect life is one she controls alone, or one she finally lets someone else help her "
        "knead into shape.",
        "short_description": "A burned-out pastry chef, a gruff harbormaster, and one summer to "
        "save a seaside bakery.",
    },
}


class FakeBackend:
    """Deterministic offline backend. Returns canned data; the Outline honors the requested
    chapter count parsed from the prompt, so offline tests aren't stuck at 3 chapters."""

    def __init__(self) -> None:
        self.usage = Usage()

    def generate(self, system: str, user: str, schema: type[T], model: str | None = None) -> T:
        # No real tokens; synthesize a deterministic placeholder so the meter plumbing is
        # exercised and testable offline (clearly synthetic, never a real bill). See ADR 0021.
        prompt_est = len(system) // 4 + len(user) // 4
        self.usage.add(prompt_est, 128, prompt_est + 128)
        if schema.__name__ == "Outline":
            return schema.model_validate(self._outline(user))
        if schema.__name__ == "SeriesPlan":
            return schema.model_validate(self._series_plan(user))
        data = _FAKE.get(schema.__name__)
        if data is None:
            raise BackendError(f"fake backend has no canned data for {schema.__name__}")
        return schema.model_validate(data)

    @staticmethod
    def _series_plan(user: str) -> dict[str, Any]:
        match = re.search(r"(\d+)\s+books", user)
        count = min(max(int(match.group(1)), 1), 12) if match else 3
        roles = ["setup", "escalation", "climax"]
        books = [
            {
                "number": i + 1,
                "title": f"The Tidewater Cycle, Book {i + 1}",
                "arc_role": roles[min(i, len(roles) - 1)] if count > 1 else "standalone",
                "premise_hint": f"Book {i + 1} raises the stakes for Brindlemouth as the "
                "season turns and old debts come due.",
            }
            for i in range(count)
        ]
        return {
            "series_title": "The Tidewater Cycle",
            "series_arc": "Across the series, a fading seaside town fights to keep its harbor, its "
            "traditions, and its people together as outside money and old grudges close in.",
            "books": books,
        }

    @staticmethod
    def _outline(user: str) -> dict[str, Any]:
        match = re.search(r"(\d+)\s+chapters", user)
        count = min(max(int(match.group(1)), 1), 60) if match else 3
        base = _FAKE["Outline"]["chapters"]
        chapters: list[dict[str, Any]] = []
        for i in range(count):
            chapter = dict(base[i % len(base)])
            chapter["number"] = i + 1
            if count > len(base):
                chapter["title"] = f"{chapter['title']} (part {i + 1})"
            chapters.append(chapter)
        return {"chapters": chapters}


def make_backend(settings: Settings) -> Backend:
    if settings.backend == "fake":
        return FakeBackend()
    return OpenAIBackend(settings)


class _BoundBackend:
    """Forces a specific model for every call — used for per-stage model overrides."""

    def __init__(self, inner: Backend, model: str) -> None:
        self._inner = inner
        self._model = model
        self.usage = inner.usage  # share the inner backend's tally (ADR 0021)

    def generate(self, system: str, user: str, schema: type[T], model: str | None = None) -> T:
        return self._inner.generate(system, user, schema, model=self._model)


def bind_model(backend: Backend, model: str | None) -> Backend:
    """Return a backend that always uses `model`, or the original backend if `model` is empty."""
    return _BoundBackend(backend, model) if model else backend
