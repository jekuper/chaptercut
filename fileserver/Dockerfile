# Build from the repository root:
#   docker compose -f fileserver/docker-compose.yml up -d --build
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
# Only the server's dependencies; the bot's are not installed here.
RUN uv sync --frozen --no-default-groups --group fileserver --no-install-project

COPY src/filexchange/ ./src/filexchange/
RUN uv sync --frozen --no-default-groups --group fileserver

RUN useradd --create-home --uid 10001 app \
 && mkdir -p /data \
 && chown -R app:app /app /data
USER app

ENTRYPOINT ["python", "-m", "filexchange"]
