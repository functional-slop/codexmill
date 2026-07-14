# CodexMill — self-hostable story-bible generator. Multi-arch (amd64 + arm64/aarch64).
# Build: docker build -t codexmill .   Run: docker run -p 8000:8000 -v codexmill-data:/data codexmill
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    CODEXMILL_CONFIG_DIR=/data \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Install dependencies first so this layer caches unless the lockfile changes.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Then the source and the project itself.
COPY src ./src
RUN uv sync --frozen --no-dev

# Run as a non-root user; /data (config store + bibles.db) is a writable named volume.
RUN useradd --create-home --uid 10001 app && mkdir -p /data && chown app:app /data
USER app
VOLUME /data
EXPOSE 8000

CMD ["uvicorn", "codexmill.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
