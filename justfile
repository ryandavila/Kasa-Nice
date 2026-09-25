# KasaBuena tasks: unprefixed commands cover the whole app; api-/web- scope them.
set positional-arguments

# List tasks by workflow
default:
    @just --list --list-heading "KasaBuena tasks:\n"

# Install development dependencies for both apps
[group('setup')]
setup: api-install web-install
    @echo "Ready: run 'just dev' for API autoreload + frontend HMR."

# Install Python dependencies, including development tools
[group('setup')]
api-install:
    uv sync --dev

# Install only Python runtime dependencies
[group('setup')]
api-install-prod:
    uv sync --no-dev

# Install frontend dependencies
[group('setup')]
web-install:
    cd web && bun install

# Build the frontend and serve it from the API
[group('run')]
run: web-build api-run

# Run the API using the current frontend build and .env settings
[group('run')]
api-run:
    uv run python -m api.main

# Run the API with autoreload
[group('run')]
api-dev port="8080":
    uv run uvicorn api.main:app --reload --host 127.0.0.1 --port "$1"

# Run API autoreload + frontend HMR; optionally choose the API port
[group('run')]
dev port="8080":
    #!/usr/bin/env bash
    set -euo pipefail
    # Backend starts from the repo root so the CWD-relative .env is picked up.
    uv run uvicorn api.main:app --reload --host 127.0.0.1 --port "$1" &
    api_pid=$!
    # Kill the API on any exit (Ctrl-C, Vite dying) so it never leaks.
    trap 'kill "$api_pid" 2>/dev/null || true; wait "$api_pid" 2>/dev/null || true' EXIT
    # Vite owns the foreground; its proxy targets whichever port the API got.
    cd web && API_PROXY_TARGET="http://127.0.0.1:${1}" bun run dev

# Run frontend HMR, proxying /api to the chosen API port
[group('run')]
web-dev port="8080":
    cd web && API_PROXY_TARGET="http://127.0.0.1:$1" bun run dev

# Build the frontend into web/build
[group('run')]
web-build:
    cd web && bun run build

# Format both apps
[group('quality')]
format: api-format web-format

# Check formatting in both apps without writing files
[group('quality')]
format-check: api-format-check web-format-check

# Lint both apps without writing files
[group('quality')]
lint: api-lint web-lint

# Apply available lint fixes in both apps
[group('quality')]
lint-fix: api-lint-fix web-lint-fix

# Check frontend types (the backend has no configured type checker)
[group('quality')]
typecheck: web-typecheck

# Run both unit test suites; browser tests are separate
[group('quality')]
test: api-test web-test

# Verify formatting, lint, types, and unit tests without writing source files
[group('quality')]
check: format-check lint typecheck test
    @echo "All checks passed."

# Apply lint fixes, format, then run all checks
[group('quality')]
fix: lint-fix format check

[group('api')]
api-format:
    uv run ruff format .

[group('api')]
api-format-check:
    uv run ruff format --check .

[group('api')]
api-lint:
    uv run ruff check --no-fix .

[group('api')]
api-lint-fix:
    uv run ruff check --fix .

# Run pytest; accepts extra pytest arguments
[group('api')]
api-test *args:
    uv run python -m pytest "$@"

[group('web')]
web-format:
    cd web && bun run format

[group('web')]
web-format-check:
    cd web && bunx prettier --check .

[group('web')]
web-lint:
    cd web && bunx eslint .

[group('web')]
web-lint-fix:
    cd web && bunx eslint --fix .

[group('web')]
web-typecheck:
    cd web && bun run check

# Run Vitest once; accepts extra Vitest arguments
[group('web')]
web-test *args:
    cd web && bun run test:run "$@"

# Run Vitest in watch mode; accepts extra Vitest arguments
[group('web')]
web-test-watch *args:
    cd web && bun run test "$@"

# Run browser tests against an isolated fake-device server
[group('browser')]
e2e port="8199": web-build (_browser-test "chromium" port)

# Regenerate docs/screenshots/*.png with isolated fake devices
[group('browser')]
screenshots port="8198": web-build (_browser-test "screenshots" port)

# Start the app with Docker Compose
[group('docker')]
docker-up:
    docker compose up -d

# Stop and remove Docker containers
[group('docker')]
docker-down:
    docker compose down

# Rebuild images from scratch and recreate containers
[group('docker')]
docker-rebuild:
    docker compose build --no-cache
    docker compose up -d --force-recreate

# Follow Docker logs
[group('docker')]
docker-logs:
    docker compose logs -f

# Open a shell in the running app container
[group('docker')]
docker-shell:
    docker compose exec kasabuena /bin/bash

# Remove Python caches and frontend build artifacts
[group('utility')]
clean: api-clean web-clean

[group('utility')]
api-clean:
    find api tests -type d -name __pycache__ -prune -exec rm -rf {} +
    rm -rf .pytest_cache .ruff_cache

[group('utility')]
web-clean:
    rm -rf web/build web/.svelte-kit

# Compatibility names stay callable without cluttering the task list.
[private]
alias install := api-install
[private]
alias dev-install := api-install
[private]
alias ci := check
[private]
alias up := docker-up
[private]
alias down := docker-down
[private]
alias rebuild := docker-rebuild
[private]
alias logs := docker-logs
[private]
alias shell := docker-shell

# Shared server lifecycle for browser tests and screenshot generation.
[private]
_browser-test project port:
    #!/usr/bin/env bash
    set -euo pipefail
    (cd web && bunx playwright install chromium)
    project="$1"
    port="$2"
    if [ "$project" = screenshots ]; then mkdir -p docs/screenshots; fi
    base="http://127.0.0.1:${port}"
    # Fake devices must never write to the real runtime state in data/.
    tmpdata="$(mktemp -d)"
    # Short intervals keep the energy/alert integration tests fast.
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
    # Always stop the server and remove its temporary state.
    trap 'kill "$server_pid" 2>/dev/null || true; wait "$server_pid" 2>/dev/null || true; rm -rf "$tmpdata"' EXIT
    for _ in $(seq 1 60); do
        if curl -sf "${base}/api/status" >/dev/null 2>&1; then ready=1; break; fi
        if ! kill -0 "$server_pid" 2>/dev/null; then echo "${project} server exited early"; exit 1; fi
        sleep 0.5
    done
    if [ "${ready:-0}" != "1" ]; then echo "${project} server did not become ready on ${base}"; exit 1; fi
    cd web && E2E_BASE_URL="$base" bunx playwright test --project="$project"
