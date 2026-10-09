.DEFAULT_GOAL := help
.PHONY: help setup lint format imports test test-extension check run extension-pack extension-install

EXTENSION_UUID := meeting-scribe@meetingscribe.dev
PACK_DIR ?= dist

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

test-extension: ## Run the GNOME extension unit tests (gjs)
	gjs -m gnome-extension/tests/run.js

check: lint test test-extension ## lint + test + test-extension

run: ## Start the local service
	uv run meeting-scribe

extension-pack: ## Build the GNOME extension zip into dist/
	mkdir -p $(PACK_DIR)
	gnome-extensions pack gnome-extension/$(EXTENSION_UUID) --extra-source=lib --force -o $(PACK_DIR)

extension-install: extension-pack ## Install the packed extension (does not enable it)
	gnome-extensions install --force $(PACK_DIR)/$(EXTENSION_UUID).shell-extension.zip
	@echo
	@echo "Installed, NOT enabled. On Wayland the shell cannot be restarted:"
	@echo "log out and back in, then run:"
	@echo "  gnome-extensions enable $(EXTENSION_UUID)"
