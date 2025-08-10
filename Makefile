.PHONY: help install dev-install run clean lint format check test docker-build docker-run docker-stop logs

# Default target
help: ## Show this help message
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# Installation commands
install: ## Install dependencies with uv
	uv sync

dev-install: ## Install development dependencies
	uv sync --dev

# Code quality commands
lint: ## Run ruff linter
	uv run ruff check  --fix .

format: ## Format code with ruff
	uv run ruff format .

# Testing commands
test: ## Run tests (when available)
	@echo "No tests configured yet. Add pytest tests in tests/ directory."

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
clean: ## Clean up cache files
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name ".pytest_cache" -delete
	rm -rf .ruff_cache

# Development workflow
setup: dev-install ## Complete development setup
	@echo "Development environment ready!"
	@echo "Run 'make run' to start the application"

