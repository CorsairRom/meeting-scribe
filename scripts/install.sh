#!/usr/bin/env bash
# One-command install: service (uv tool), systemd --user unit, D-Bus activation, GNOME extension.
# Idempotent; safe to re-run to upgrade. Never restarts the service while a recording is running.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/_common.sh
source "$REPO_ROOT/scripts/_common.sh"

echo "==> Installing the service with uv (Python 3.12)"
uv tool install --force --reinstall --python 3.12 "$REPO_ROOT"
EXEC_START="$(uv tool dir --bin)/meeting-scribe"
if [[ ! -x "$EXEC_START" ]]; then
  echo "error: $EXEC_START was not created by uv tool install" >&2
  exit 1
fi

echo "==> Installing the systemd user unit and the D-Bus activation file"
mkdir -p "$(dirname "$UNIT_PATH")" "$(dirname "$ACTIVATION_PATH")"
sed "s|@EXEC_START@|$EXEC_START|" "$REPO_ROOT/packaging/systemd/$UNIT_NAME.in" >"$UNIT_PATH"
cp "$REPO_ROOT/packaging/dbus/$BUS_NAME.service" "$ACTIVATION_PATH"

echo "==> Enabling and starting the service"
was_active=0
unit_active && was_active=1
systemctl --user daemon-reload
service_note=""
if ((was_active)); then
  systemctl --user enable "$UNIT_NAME"
  if is_recording; then
    service_note="NOT restarted: a recording is in progress. Stop the recording first, then run 'make install' again (or 'systemctl --user restart $UNIT_NAME')."
    echo "warning: $service_note" >&2
  else
    systemctl --user restart "$UNIT_NAME"
  fi
else
  systemctl --user enable --now "$UNIT_NAME"
fi

echo "==> Installing the GNOME extension"
pack_dir="$(mktemp -d)"
trap 'rm -rf "$pack_dir"' EXIT
gnome-extensions pack "$REPO_ROOT/gnome-extension/$EXTENSION_UUID" --extra-source=lib --force \
  -o "$pack_dir"
gnome-extensions install --force "$pack_dir/$EXTENSION_UUID.shell-extension.zip"
extension_state="enabled"
if ! gnome-extensions enable "$EXTENSION_UUID" 2>/dev/null \
  || ! gnome-extensions info "$EXTENSION_UUID" >/dev/null 2>&1; then
  extension_state="installed, will appear after you log out and back in"
  echo "Extension installed: log out and back in once so GNOME Shell discovers it (the service is already running)."
fi

echo
echo "meeting-scribe installed"
if unit_active; then
  echo "  service:   active ($UNIT_NAME)"
else
  echo "  service:   NOT active (check: systemctl --user status $UNIT_NAME)"
fi
[[ -z "$service_note" ]] || echo "  note:      $service_note"
echo "  extension: $extension_state ($EXTENSION_UUID)"
echo "  config:    $CONFIG_HOME/meeting-scribe/.env (optional, see .env.example)"
