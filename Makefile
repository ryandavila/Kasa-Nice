.PHONY: help install dev-install web-install run dev api-dev web-dev web-build \
        clean lint format web-lint web-format check test up down rebuild logs shell setup

# Default target
help: ## Show this help message
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# Installation commands
install: ## Install Python dependencies with uv
	uv sync

dev-install: ## Install Python development dependencies
	uv sync --dev

web-install: ## Install frontend dependencies with bun
	cd web && bun install

# Run commands
run: web-build ## Build frontend and run the API serving it (production-style)
	uv run python -m api.main

api-dev: ## Run the API with autoreload (frontend served separately via web-dev)
	uv run uvicorn api.main:app --reload --host 127.0.0.1 --port 8080

web-dev: ## Run the SvelteKit dev server (proxies /api to the backend)
	cd web && bun run dev

web-build: ## Build the frontend into web/build
	cd web && bun run build

# Code quality commands
lint: ## Run ruff linter on the Python backend
	uv run ruff check --fix .

format: ## Format Python code with ruff
	uv run ruff format .

web-lint: ## Lint the frontend with prettier + eslint
	cd web && bun run lint

web-format: ## Format the frontend with prettier
	cd web && bun run format

check: ## Type-check the frontend
	cd web && bun run check

# Testing commands
test: ## Run the backend test suite with pytest
	uv run pytest

# Docker commands
up: ## Start application with Docker Compose
	docker compose up -d

down: ## Stop and remove Docker containers
	docker compose down

rebuild: ## Rebuild and restart Docker containers
	docker compose down
	docker compose build --no-cache
	docker compose up -d

logs: ## View Docker logs
	docker compose logs -f

shell: ## Get shell access to running container
	docker compose exec kasa-nice /bin/bash

# Utility commands
clean: ## Clean up cache and build artifacts
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name ".pytest_cache" -delete
	rm -rf .ruff_cache web/build web/.svelte-kit

# Development workflow
setup: dev-install web-install ## Complete development setup (Python + frontend)
	@echo "Development environment ready!"
	@echo "Run 'make api-dev' and 'make web-dev' in two terminals to start developing."
