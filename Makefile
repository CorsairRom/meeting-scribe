.DEFAULT_GOAL := help
.PHONY: help setup lint format imports test check run

help: ## List available targets
	@awk -F':.*## ' '/^[a-z-]+:.*## / {printf "%s\t%s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Install runtime and quality-assurance dependencies
	uv sync --extra quality-assurance

lint: ## ruff, pyrefly and import-linter
	scripts/run-linter.sh

format: ## Auto-format and fix lint issues
	uv run --extra quality-assurance ruff format src tests
	uv run --extra quality-assurance ruff check --fix src tests

imports: ## Check architecture contracts only
	uv run --extra quality-assurance lint-imports

test: ## Run the test suite
	scripts/run-tests.sh

check: lint test ## lint + test

run: ## Start the local service
	uv run meeting-scribe
