#!/usr/bin/env bash
# The one entry point: a thin wrapper over scripts/uninstall.sh (see docs/specs/install.md section 3).
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts/uninstall.sh" "$@"
