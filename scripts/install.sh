#!/usr/bin/env bash
# One-command install: service (uv tool), systemd --user unit, D-Bus activation, GNOME extension.
# Idempotent; safe to re-run to upgrade. Refuses to run while a recording is in progress, or its
# state is unknown unless FORCE=1 (FORCE never overrides a recording).
#
# Every run installs into a fresh versions/<id>/ directory and then repoints the `current` symlink
# atomically, so the environment a running service was loaded from is never replaced. Old versions
# are pruned only once the service runs the new one.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/_common.sh
source "$REPO_ROOT/scripts/_common.sh"

# Decide before touching anything.
refuse_unless_safe "'make install'" unknown || exit 1

version="$(date +%Y%m%d%H%M%S)-$$"
version_dir="$VERSIONS_DIR/$version"
echo "==> Installing the service with uv (Python 3.12) into $version_dir"
mkdir -p "$VERSIONS_DIR"
if ! UV_TOOL_DIR="$version_dir/tools" UV_TOOL_BIN_DIR="$version_dir/bin" \
  uv tool install --force --reinstall --python 3.12 "$REPO_ROOT"; then
  rm -rf "$version_dir"
  echo "error: uv tool install failed; the installed version was left untouched." >&2
  exit 1
fi
if [[ ! -x "$version_dir/bin/meeting-scribe" ]]; then
  rm -rf "$version_dir"
  echo "error: $version_dir/bin/meeting-scribe was not created by uv tool install" >&2
  exit 1
fi

# A unit rendered by a pre-versioning install points at the old uv tool; remember that to migrate.
legacy_tool=0
if [[ -f "$UNIT_PATH" ]] && ! grep -qFx "ExecStart=$EXEC_START" "$UNIT_PATH"; then
  legacy_tool=1
fi

# Atomic switch: a running service keeps using the real versioned path it started from.
ln -s "versions/$version" "$INSTALL_ROOT/current.new.$$"
mv -T "$INSTALL_ROOT/current.new.$$" "$CURRENT_LINK"

echo "==> Installing the systemd user unit and the D-Bus activation file"
mkdir -p "$(dirname "$UNIT_PATH")" "$(dirname "$ACTIVATION_PATH")"
# Render in bash (no sed replacement): the path may contain '&', '|' or '\'.
template="$(<"$REPO_ROOT/packaging/systemd/$UNIT_NAME.in")"
printf '%s\n' "${template%%@EXEC_START@*}$EXEC_START${template#*@EXEC_START@}" >"$UNIT_PATH"
if ! grep -qFx "ExecStart=$EXEC_START" "$UNIT_PATH"; then
  echo "error: the rendered unit does not contain ExecStart=$EXEC_START" >&2
  exit 1
fi
cp "$REPO_ROOT/packaging/dbus/$BUS_NAME.service" "$ACTIVATION_PATH"

echo "==> Enabling and starting the service"
was_active=0
unit_active && was_active=1
start_failed=0
reload_failed=0
restart_skipped=0
activated=0
systemctl --user daemon-reload || reload_failed=1
if ((was_active)); then
  if systemctl --user enable "$UNIT_NAME"; then
    # Re-check: a recording may have started while uv was installing. Never kill it.
    # FORCE=1 also replaces a wedged service whose state stays unreadable, never a recording.
    restart_state="$(service_state)"
    if [[ "$restart_state" == unknown && "${FORCE:-0}" == 1 ]]; then
      restart_state=idle
    fi
    case "$restart_state" in
      none | idle) systemctl --user restart "$UNIT_NAME" && activated=1 || start_failed=1 ;;
      *)
        restart_skipped=1
        echo "warning: not restarting $UNIT_NAME because a recording is running or its state is unknown; the new version takes effect on the next restart or login." >&2 ;;
    esac
  else
    start_failed=1
  fi
else
  systemctl --user enable --now "$UNIT_NAME" && activated=1 || start_failed=1
fi

migration_note=""
if ((activated)) && unit_active; then
  # The service now runs the new version: older versions and the legacy uv tool are unused.
  for old in "$VERSIONS_DIR"/*; do
    [[ -d "$old" && "$old" != "$version_dir" ]] && rm -rf "$old"
  done
  if ((legacy_tool)); then
    uv tool uninstall meeting-scribe || migration_note="old uv tool could not be removed (uv tool uninstall meeting-scribe)"
  fi
elif ((legacy_tool)); then
  migration_note="the old uv tool is kept while the old service may still run; remove it later with: uv tool uninstall meeting-scribe"
fi

echo "==> Installing the GNOME extension"
extension_failed=""
extension_state="enabled"
pack_dir="$(mktemp -d)"
trap 'rm -rf "$pack_dir"' EXIT
if ! gnome-extensions pack "$REPO_ROOT/gnome-extension/$EXTENSION_UUID" --extra-source=lib --force \
  -o "$pack_dir"; then
  extension_failed="pack failed (gnome-extensions pack)"
elif ! gnome-extensions install --force "$pack_dir/$EXTENSION_UUID.shell-extension.zip"; then
  extension_failed="install failed (gnome-extensions install)"
elif ! gnome-extensions enable "$EXTENSION_UUID" 2>/dev/null \
  || ! gnome-extensions info "$EXTENSION_UUID" >/dev/null 2>&1; then
  extension_state="installed, will appear after you log out and back in"
  if unit_active; then
    echo "Extension installed: log out and back in once so GNOME Shell discovers it (the service is already running)."
  else
    echo "Extension installed: log out and back in once so GNOME Shell discovers it (the service is NOT active, see the summary)."
  fi
fi

echo
echo "meeting-scribe installed"
echo "  version:   $version"
if unit_active; then
  echo "  service:   active ($UNIT_NAME)"
else
  echo "  service:   NOT active (check: systemctl --user status $UNIT_NAME)"
  if ((start_failed)); then
    echo "  cause:     starting the service failed. Possible causes: another instance (for example"
    echo "             'make run') owns the bus name (stop it with Ctrl+C, then run"
    echo "             'systemctl --user restart $UNIT_NAME'), or the unit or executable is broken."
    echo "             Details: journalctl --user -u $UNIT_NAME"
  fi
fi
if ((restart_skipped)); then
  echo "  restart:   skipped (recording or unknown state); the new version takes effect on the next restart or login"
fi
if ((reload_failed)); then
  echo "  cause:     daemon-reload failed (systemctl --user daemon-reload); the unit file may not be loaded."
fi
if [[ -n "$migration_note" ]]; then
  echo "  migration: $migration_note"
fi
if [[ -n "$extension_failed" ]]; then
  echo "  extension: FAILED ($extension_failed)"
else
  echo "  extension: $extension_state ($EXTENSION_UUID)"
fi
echo "  config:    $CONFIG_HOME/meeting-scribe/.env (optional, see .env.example)"
if ((start_failed || reload_failed)) || [[ -n "$extension_failed" ]]; then
  exit 1
fi
