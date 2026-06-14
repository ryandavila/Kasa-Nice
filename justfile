# Kasa-Nice task runner. Run `just` to list recipes.

# Show available recipes
default:
    @just --list

# --- Installation ---

# Install Python dependencies with uv
install:
    uv sync

# Install Python development dependencies
dev-install:
    uv sync --dev

# Install frontend dependencies with bun
web-install:
    cd web && bun install

# Complete development setup (Python + frontend)
setup: dev-install web-install
    @echo "Development environment ready!"
    @echo "Run 'just api-dev' and 'just web-dev' in two terminals to start developing."

# --- Run ---

# Build frontend and run the API serving it (production-style)
run: web-build
    uv run python -m api.main

# Run the API with autoreload (frontend served separately via web-dev)
api-dev:
    uv run uvicorn api.main:app --reload --host 127.0.0.1 --port 8080

# Run the SvelteKit dev server (proxies /api to the backend)
web-dev:
    cd web && bun run dev

# Build the frontend into web/build
web-build:
    cd web && bun run build

# --- Code quality ---

# Format Python (ruff) and the frontend (prettier)
format:
    uv run ruff format .
    cd web && bun run format

# Lint and autofix Python (ruff) and lint the frontend (prettier + eslint)
lint:
    uv run ruff check --fix .
    cd web && bun run lint

# Type-check the frontend (svelte-check)
typecheck:
    cd web && bun run check

# Run the backend test suite with pytest
test:
    uv run pytest

# Format, lint, type-check, and test everything
fix: format lint typecheck test
    @echo "All checks passed."

# Verify formatting, lint, types, and tests without mutating files (used by CI)
ci:
    uv run ruff format --check .
    uv run ruff check .
    cd web && bun run lint
    cd web && bun run check
    uv run pytest
    @echo "All checks passed."

# --- Docker ---

# Start application with Docker Compose
up:
    docker compose up -d

# Stop and remove Docker containers
down:
    docker compose down

# Rebuild and restart Docker containers
rebuild:
    docker compose down
    docker compose build --no-cache
    docker compose up -d

# View Docker logs
logs:
    docker compose logs -f

# Get shell access to running container
shell:
    docker compose exec kasa-nice /bin/bash

# --- Utility ---

# Clean up cache and build artifacts
clean:
    find . -type f -name "*.pyc" -delete
    find . -type d -name "__pycache__" -delete
    find . -type d -name ".pytest_cache" -delete
    rm -rf .ruff_cache web/build web/.svelte-kit
