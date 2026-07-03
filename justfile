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
    @echo "Run 'just dev' to start developing (API autoreload + frontend HMR)."

# --- Run ---

# Build frontend and run the API serving it (production-style)
run: web-build
    uv run python -m api.main

# Run the API with autoreload (frontend served separately via web-dev)
api-dev:
    uv run uvicorn api.main:app --reload --host 127.0.0.1 --port 8080

# Run the API (autoreload) and the Vite dev server (HMR) together; Ctrl-C stops
# both. Pass a port when 8080 is taken (e.g. by the Docker instance): `just dev 8090`.
dev port="8080":
    #!/usr/bin/env bash
    set -euo pipefail
    # Backend starts from the repo root so the CWD-relative .env is picked up.
    uv run uvicorn api.main:app --reload --host 127.0.0.1 --port {{port}} &
    api_pid=$!
    # Kill the API on any exit (Ctrl-C, Vite dying) so it never leaks.
    trap 'kill "$api_pid" 2>/dev/null || true; wait "$api_pid" 2>/dev/null || true' EXIT
    # Vite owns the foreground; its proxy targets whichever port the API got.
    cd web && API_PROXY_TARGET="http://127.0.0.1:{{port}}" bun run dev

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

# Run the frontend unit tests with vitest
web-test:
    cd web && bun run test:run

# Run the Playwright end-to-end suite against the production-style server.
# Builds the SPA, starts the API serving it with in-process fake devices (no
# hardware/credentials needed), waits for it, runs every spec except
# screenshots.spec.ts (see the `screenshots` recipe), and always tears the
# server down.
e2e: web-build
    #!/usr/bin/env bash
    set -euo pipefail
    # Idempotent: downloads Chromium on first run, near-instant no-op after. Done
    # before the server starts so a slow first download isn't holding a server.
    (cd web && bunx playwright install chromium)
    # An uncommon port so we don't collide with (and silently test against) a real
    # Kasa-Nice instance a developer may have running on the usual 8080.
    port=8199
    base="http://127.0.0.1:${port}"
    # Point every persistence store at a throwaway directory: the fake-device
    # server WRITES these files (snapshot store, energy recorder, specs creating
    # scenes/schedules/alerts), and the defaults live in data/ — the real runtime
    # state that the Docker instance mounts.
    tmpdata="$(mktemp -d)"
    # The alerts spec needs a real energy-sample + alert-evaluator cycle against
    # the fake registry; both intervals are floored at 10s (see api/config.py),
    # so set them there to keep that spec's wait fast rather than the ~30-60s
    # production defaults.
    KASA_FAKE_DEVICES=1 KASA_HOST=127.0.0.1 KASA_PORT="$port" \
        KASA_ENERGY_SAMPLE_INTERVAL=10 KASA_ALERT_INTERVAL=10 \
        KASA_STATE_FILE="$tmpdata/known_devices.json" \
        KASA_SNAPSHOT_FILE="$tmpdata/device_snapshots.json" \
        KASA_GROUPS_FILE="$tmpdata/groups.json" \
        KASA_ENERGY_HISTORY_FILE="$tmpdata/energy_history.db" \
        KASA_SCHEDULES_FILE="$tmpdata/schedules.json" \
        KASA_ALERTS_FILE="$tmpdata/alerts.json" \
        KASA_SCENES_FILE="$tmpdata/scenes.json" \
        KASA_VACATION_FILE="$tmpdata/vacation.json" \
        uv run python -m api.main &
    server_pid=$!
    # Kill the server and drop its scratch state on any exit (success, failure,
    # or interrupt) so neither ever leaks.
    trap 'kill "$server_pid" 2>/dev/null || true; wait "$server_pid" 2>/dev/null || true; rm -rf "$tmpdata"' EXIT
    # Wait for OUR server to accept connections; fail loudly if it never binds
    # (e.g. the port was taken) rather than testing against something else.
    for _ in $(seq 1 60); do
        if curl -sf "${base}/api/status" >/dev/null 2>&1; then ready=1; break; fi
        if ! kill -0 "$server_pid" 2>/dev/null; then echo "e2e server exited early"; exit 1; fi
        sleep 0.5
    done
    if [ "${ready:-0}" != "1" ]; then echo "e2e server did not become ready on ${base}"; exit 1; fi
    # --project=chromium: Playwright runs every project with no filter, and the
    # `screenshots` project (screenshots.spec.ts) must never join this pass/fail run.
    cd web && E2E_BASE_URL="$base" bunx playwright test --project=chromium

# Regenerate the README hero screenshots (docs/screenshots/*.png). Boots the
# same fake-device server as `just e2e` but runs only screenshots.spec.ts (via
# the `screenshots` Playwright project), so it never affects e2e's pass/fail
# signal. Commit the resulting PNGs.
screenshots: web-build
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p docs/screenshots
    (cd web && bunx playwright install chromium)
    port=8198
    base="http://127.0.0.1:${port}"
    # Same throwaway persistence directory as `just e2e`: the fake server writes
    # these files, and the defaults are the real runtime state in data/.
    tmpdata="$(mktemp -d)"
    KASA_FAKE_DEVICES=1 KASA_HOST=127.0.0.1 KASA_PORT="$port" \
        KASA_STATE_FILE="$tmpdata/known_devices.json" \
        KASA_SNAPSHOT_FILE="$tmpdata/device_snapshots.json" \
        KASA_GROUPS_FILE="$tmpdata/groups.json" \
        KASA_ENERGY_HISTORY_FILE="$tmpdata/energy_history.db" \
        KASA_SCHEDULES_FILE="$tmpdata/schedules.json" \
        KASA_ALERTS_FILE="$tmpdata/alerts.json" \
        KASA_SCENES_FILE="$tmpdata/scenes.json" \
        KASA_VACATION_FILE="$tmpdata/vacation.json" \
        uv run python -m api.main &
    server_pid=$!
    trap 'kill "$server_pid" 2>/dev/null || true; wait "$server_pid" 2>/dev/null || true; rm -rf "$tmpdata"' EXIT
    for _ in $(seq 1 60); do
        if curl -sf "${base}/api/status" >/dev/null 2>&1; then ready=1; break; fi
        if ! kill -0 "$server_pid" 2>/dev/null; then echo "screenshots server exited early"; exit 1; fi
        sleep 0.5
    done
    if [ "${ready:-0}" != "1" ]; then echo "screenshots server did not become ready on ${base}"; exit 1; fi
    cd web && E2E_BASE_URL="$base" bunx playwright test --project=screenshots

# Format, lint, type-check, and test everything
fix: format lint typecheck test web-test
    @echo "All checks passed."

# Verify formatting, lint, types, and tests without mutating files (used by CI)
ci:
    uv run ruff format --check .
    uv run ruff check .
    cd web && bun run lint
    cd web && bun run check
    cd web && bun run test:run
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
