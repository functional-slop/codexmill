# 3. Backend-agnostic LLM layer

Date: 2026-07-10 · Status: accepted

## Context
For the tool to actually saturate the market it must run for anyone: on a free cloud tier
(Gemini/Groq), on a modest laptop via local Ollama, or on a big local box. Hardcoding a
provider would tie it to the developer's machine.

## Decision
All model access goes through a `Backend` protocol (`src/codexmill/llm.py`). The concrete
`OpenAIBackend` targets any OpenAI-compatible `/v1` endpoint, configured purely via env:
`CODEXMILL_BASE_URL`, `CODEXMILL_MODEL`, `CODEXMILL_API_KEY`. A `FakeBackend`
(`CODEXMILL_BACKEND=fake`) provides deterministic offline output for tests and demos.
No provider name, URL, or model is hardcoded in stage logic.

## Consequences
- One codebase runs on all three tiers; the README documents each.
- Tests run offline and deterministically against the fake backend, driving the real CLI.
- Structured output is enforced app-side (ask for JSON → validate with pydantic → retry) rather
  than relying on any one provider's schema feature, keeping portability.
