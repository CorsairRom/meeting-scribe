#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv run --extra quality-assurance ruff check src tests review-app
uv run --extra quality-assurance ruff format --check src tests review-app
uv run --extra quality-assurance pyrefly check
PYTHONPATH=review-app/src uv run --extra quality-assurance lint-imports
