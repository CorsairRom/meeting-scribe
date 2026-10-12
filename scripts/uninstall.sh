#!/usr/bin/env bash
# Reverses install.sh. Idempotent. Never touches recorded meetings or the user's .env.
# Refuses while a recording is running or its state cannot be read, unless FORCE=1 (which here
# overrides both: uninstalling is destructive by intent).
# Run it as ./uninstall.sh (the root wrapper) or `make uninstall` (alias).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/_common.sh
source "$REPO_ROOT/scripts/_common.sh"

usage() {
  cat <<'USAGE'
Usage: ./uninstall.sh [--help]   (alias: make uninstall)

Removes what ./install.sh created. It keeps your meetings, settings.json, .env, the keyring items
(cloud API key and Hugging Face token) and the downloaded models in ~/.cache/huggingface. FORCE=1 ./uninstall.sh stops it even while a recording runs.
USAGE
}
for arg in "$@"; do
  case "$arg" in
    -h | --help) usage; exit 0 ;;
    *) echo "error: unknown option: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

refuse_unless_safe "'./uninstall.sh'" recording "Or run 'FORCE=1 ./uninstall.sh' to stop it anyway." || exit 1

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
echo "meeting-scribe uninstalled."
echo "Kept (not removed):"
echo "  - your meetings (the meetings folder is never touched)"
echo "  - settings.json and .env in $CONFIG_HOME/meeting-scribe"
echo "  - the keyring items: the cloud API key and the Hugging Face token (remove them in Seahorse)"
echo "  - the downloaded models in ~/.cache/huggingface (shared with other tools)"
