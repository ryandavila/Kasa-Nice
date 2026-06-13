# Stage 1: build the SvelteKit frontend into a static SPA (web/build).
FROM oven/bun:1 AS web
WORKDIR /web
COPY web/package.json web/bun.lock ./
RUN bun install --frozen-lockfile
COPY web/ ./
RUN bun run build

# Stage 2: Python runtime that serves the API and the built frontend.
FROM python:3.14-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Install Python dependencies
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Application code
COPY api/ ./api/

# Built frontend from stage 1 (served by the API at /)
COPY --from=web /web/build ./web/build

EXPOSE 8080

ENV PYTHONUNBUFFERED=1 \
    KASA_HOST=0.0.0.0 \
    KASA_PORT=8080

CMD ["uv", "run", "python", "-m", "api.main"]
