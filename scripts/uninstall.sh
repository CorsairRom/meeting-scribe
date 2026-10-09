#!/usr/bin/env bash
# Reverses install.sh. Idempotent. Never touches recorded meetings or the user's .env.
# Refuses while a recording is running or its state cannot be read, unless FORCE=1.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/_common.sh
source "$REPO_ROOT/scripts/_common.sh"

if [[ "${FORCE:-0}" != 1 ]]; then
  refuse_unless_safe "'make uninstall'" "Or run 'make uninstall FORCE=1' to stop it anyway." || exit 1
fi

echo "==> Stopping the service and removing the unit and activation file"
systemctl --user disable --now "$UNIT_NAME" || true
rm -f "$UNIT_PATH" "$ACTIVATION_PATH"
systemctl --user daemon-reload || true

echo "==> Removing the GNOME extension"
gnome-extensions disable "$EXTENSION_UUID" || true
gnome-extensions uninstall "$EXTENSION_UUID" || true

echo "==> Removing the uv tool"
uv tool uninstall meeting-scribe || true

echo
echo "meeting-scribe uninstalled. Your meetings and $CONFIG_HOME/meeting-scribe/.env were left untouched."
