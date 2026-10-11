#!/usr/bin/env bash
# Reverses install.sh. Idempotent. Never touches recorded meetings or the user's .env.
# Refuses while a recording is running or its state cannot be read, unless FORCE=1 (which here
# overrides both: uninstalling is destructive by intent).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/_common.sh
source "$REPO_ROOT/scripts/_common.sh"

refuse_unless_safe "'make uninstall'" recording "Or run 'make uninstall FORCE=1' to stop it anyway." || exit 1

echo "==> Stopping the service and removing the unit and activation file"
systemctl --user disable --now "$UNIT_NAME" || true
rm -f "$UNIT_PATH" "$ACTIVATION_PATH"
systemctl --user daemon-reload || true

echo "==> Removing the GNOME extension"
gnome-extensions disable "$EXTENSION_UUID" || true
gnome-extensions uninstall "$EXTENSION_UUID" || true

echo "==> Removing the review app desktop entry"
rm -f "$DESKTOP_ENTRY_PATH"
refresh_desktop_database

echo "==> Removing the installed versions (the review app and its launcher are inside)"
# Remove only what install.sh created; anything else under $INSTALL_ROOT (e.g. caches) stays.
rm -f "$CURRENT_LINK"
rm -rf "$VERSIONS_DIR"
rmdir "$INSTALL_ROOT" 2>/dev/null || true
# A pre-versioning install lives in the default uv tool directory: remove it too (best effort).
uv tool uninstall meeting-scribe || true

echo
echo "meeting-scribe uninstalled. Your meetings and $CONFIG_HOME/meeting-scribe/.env were left untouched."
