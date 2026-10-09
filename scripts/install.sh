#!/usr/bin/env bash
# One-command install: service (uv tool), systemd --user unit, D-Bus activation, GNOME extension.
# Idempotent; safe to re-run to upgrade. Refuses to run while a recording is in progress, or its state is unknown unless FORCE=1.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/_common.sh
source "$REPO_ROOT/scripts/_common.sh"

# Decide before touching anything: reinstalling replaces the environment a live service runs from.
refuse_unless_safe "'make install'" || exit 1

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
start_failed=0
reload_failed=0
restart_skipped=0
systemctl --user daemon-reload || reload_failed=1
if ((was_active)); then
  if systemctl --user enable "$UNIT_NAME"; then
    # Re-check: a recording may have started while uv was reinstalling. Never kill it.
    case "$(service_state)" in
      none | idle) systemctl --user restart "$UNIT_NAME" || start_failed=1 ;;
      *)
        restart_skipped=1
        echo "warning: not restarting $UNIT_NAME because a recording is running or its state is unknown; the new version takes effect on the next restart or login." >&2 ;;
    esac
  else
    start_failed=1
  fi
else
  systemctl --user enable --now "$UNIT_NAME" || start_failed=1
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
  if ((start_failed)); then
    echo "  cause:     starting the service failed. Another instance (for example 'make run')"
    echo "             may already own the bus name: stop it with Ctrl+C and run"
    echo "             'systemctl --user restart $UNIT_NAME'."
  fi
fi
if ((restart_skipped)); then
  echo "  restart:   skipped (recording or unknown state); the new version takes effect on the next restart or login"
fi
if ((reload_failed)); then
  echo "  cause:     daemon-reload failed (systemctl --user daemon-reload); the unit file may not be loaded."
fi
echo "  extension: $extension_state ($EXTENSION_UUID)"
echo "  config:    $CONFIG_HOME/meeting-scribe/.env (optional, see .env.example)"
if ((start_failed || reload_failed)); then
  exit 1
fi
