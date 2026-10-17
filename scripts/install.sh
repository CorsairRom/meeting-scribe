#!/usr/bin/env bash
# One-command install: service (uv tool), systemd --user unit, D-Bus activation, review app, and the
# GNOME extension on GNOME Shell or the tray (with its autostart entry) on any other desktop.
# Run it as ./install.sh (the root wrapper) or `make install` (alias; `make install ARGS=--check`).
# Idempotent; safe to re-run to upgrade. It first runs the read-only preflight (scripts/preflight.sh)
# and stops with zero changes when a prerequisite is missing; it never runs sudo and never prompts.
# Then it refuses to run while a recording is in progress, or its state is unknown unless FORCE=1
# (FORCE never overrides a recording).
#
# Every run installs into a fresh versions/<id>/ directory, verifies it, and only then repoints the
# `current` symlink atomically, so the environment a running service was loaded from is never
# replaced. Old versions are pruned only once the service runs the new one. If the new version
# fails to start, `current` (or, on a migration, the unit) is restored to what worked before.
#
# The install never downloads the ML models and never talks to the network: recording needs none of
# them. After the switch it only runs `prefetch-models --check` (offline, bounded by
# MEETING_SCRIBE_MODELS_CHECK_TIMEOUT seconds, default 30) and reports `models: ready (cached)` or
# `models: PENDING`. The models arrive from the Preferences window (Download models).
#
# Ollama and the local summary model are checked by the preflight (read-only: one GET of /api/tags):
# the install never starts Ollama, never pulls a model and never sends a prompt. A gap stops the
# install before any change, with the command to fix it printed.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/_common.sh
source "$REPO_ROOT/scripts/_common.sh"

# shellcheck source=scripts/preflight.sh
source "$REPO_ROOT/scripts/preflight.sh"

usage() {
  cat <<'USAGE'
Usage: ./install.sh [--check [--json]] [--help]   (alias: make install ARGS=--check)

Installs the service, the systemd user unit, the D-Bus activation file, the review app and the
GNOME extension (outside GNOME Shell, the tray instead). It first checks every prerequisite
(read-only) and stops with zero changes when one is missing. It never elevates privileges and
never asks anything: it prints the commands to run.

  --check   Run only the preflight, change nothing, call no service.
  --json    With --check: print one JSON document on stdout (the prose goes to stderr).
  --help    Show this text.

Environment: FORCE=1 installs over an unreadable recording state (never over a recording).

Exit codes:
  0   success: installed, or --check found nothing that blocks
  1   an install stage failed or was refused (recording in progress, service, extension or tray step)
  2   usage error
  10  blocked: at least one FIX item (a printed command fixes it)
  11  blocked: only HUMAN items remain (only the user can resolve them)
USAGE
}

check_only=0
json_output=0
for arg in "$@"; do
  case "$arg" in
    --check) check_only=1 ;;
    --json) json_output=1 ;;
    -h | --help) usage; exit 0 ;;
    *) echo "error: unknown option: $arg" >&2; usage >&2; exit 2 ;;
  esac
done
if ((json_output && !check_only)); then
  echo "error: --json is only valid with --check" >&2
  usage >&2
  exit 2
fi

# The preflight comes first and is read-only: nothing below runs while a prerequisite is missing.
preflight_run
preflight_code="$(pf_exit_code)"
if ((check_only)); then
  if ((json_output)); then
    pf_report json
    pf_report text >&2
  else
    pf_report text
  fi
  exit "$preflight_code"
fi
if ((preflight_code != 0)); then
  pf_report text
  exit "$preflight_code"
fi
echo "==> Prerequisites: all present"
pf_items_text WARN

# Decide before touching anything.
refuse_unless_safe "'./install.sh'" unknown || exit 1

# Repoints `current` atomically through a uniquely named temporary link, so a leftover link from
# an interrupted run can never collide. $1 is the link target, relative to $INSTALL_ROOT.
point_current() {
  local link_tmp
  link_tmp="$(mktemp -u "$INSTALL_ROOT/current.new.XXXXXX")"
  ln -s "$1" "$link_tmp"
  mv -T "$link_tmp" "$CURRENT_LINK"
}

# The target `current` points at now (a working versioned install), or empty on a first install
# or a migration. It is what a failed start rolls back to.
previous=""
if [[ -L "$CURRENT_LINK" && -d "$CURRENT_LINK" ]]; then
  previous="$(readlink "$CURRENT_LINK")"
fi

# The legacy install: a unit from before versioned installs whose ExecStart is exactly the default
# uv tool bin directory's executable (queried without the versioned UV_TOOL_* variables). A
# hand-edited or foreign unit is not the legacy install and never triggers `uv tool uninstall`.
legacy_tool=0
legacy_exec=""
unit_backup=""
if [[ -f "$UNIT_PATH" ]]; then
  unit_backup="$(<"$UNIT_PATH")"
  if ! grep -qFx "ExecStart=$EXEC_START" "$UNIT_PATH"; then
    legacy_bin_dir="$(env -u UV_TOOL_DIR -u UV_TOOL_BIN_DIR uv tool dir --bin 2>/dev/null)" || legacy_bin_dir=""
    if [[ -n "$legacy_bin_dir" ]] && grep -qFx "ExecStart=$legacy_bin_dir/meeting-scribe" "$UNIT_PATH"; then
      legacy_tool=1
      legacy_exec="$legacy_bin_dir/meeting-scribe"
    fi
  fi
fi

version="$(date +%Y%m%d%H%M%S)-$$"
version_dir="$VERSIONS_DIR/$version"

# Before the switch nothing is changed: drop the new directory and stop.
abort_before_switch() {
  rm -rf "$version_dir"
  echo "error: $1; the installed version was left untouched." >&2
  exit 1
}

echo "==> Installing the service with uv (Python 3.12) into $version_dir"
mkdir -p "$VERSIONS_DIR"
if ! UV_TOOL_DIR="$version_dir/tools" UV_TOOL_BIN_DIR="$version_dir/bin" \
  uv tool install --force --reinstall --python 3.12 "meeting-scribe[ml] @ $REPO_ROOT"; then
  abort_before_switch "uv tool install failed"
fi
if [[ ! -x "$version_dir/bin/meeting-scribe" ]]; then
  abort_before_switch "$version_dir/bin/meeting-scribe was not created by uv tool install"
fi

# The review app and its launcher live in the same version directory as the service, so a rollback
# restores both and `current` switches them together. The launcher is rendered in bash (printf %q
# keeps any path character intact) and pins the system interpreter on purpose.
echo "==> Installing the review app into $version_dir/app"
app_dir="$version_dir/app"
launcher="$version_dir/bin/$REVIEW_LAUNCHER_NAME"
if ! mkdir -p "$app_dir" || ! cp -r "$REPO_ROOT/review-app/src/meeting_scribe_review" "$app_dir/"; then
  abort_before_switch "the review app could not be copied"
fi
shopt -s globstar nullglob
rm -rf "$app_dir"/**/__pycache__
shopt -u globstar nullglob
{
  echo '#!/usr/bin/env bash'
  printf 'PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=%q exec %q -m meeting_scribe_review "$@"\n' \
    "$app_dir" "$REVIEW_APP_PYTHON"
} >"$launcher"
chmod +x "$launcher"

# Outside GNOME Shell the tray goes into the same version directory (docs/specs/desktop-tray.md
# section 1): desktop-tray/ without its tests, and the extension's shared lib/ at the same relative
# path as in the repository, so the tray's relative imports resolve unchanged. Its launcher runs the
# tray through the stable current/ path, as the autostart entry does.
if [[ "$PF_DESKTOP" == tray ]]; then
  echo "==> Installing the tray into $version_dir/desktop-tray"
  tray_dir="$version_dir/desktop-tray"
  shared_lib_parent="$version_dir/gnome-extension/$EXTENSION_UUID"
  tray_copied=1
  mkdir -p "$tray_dir" "$shared_lib_parent" || tray_copied=0
  for entry in "$REPO_ROOT/desktop-tray"/*; do
    if [[ "${entry##*/}" != tests ]]; then cp -r "$entry" "$tray_dir/" || tray_copied=0; fi
  done
  cp -r "$REPO_ROOT/gnome-extension/$EXTENSION_UUID/lib" "$shared_lib_parent/" || tray_copied=0
  if ((!tray_copied)); then
    abort_before_switch "the tray could not be copied"
  fi
  tray_launcher="$version_dir/bin/$TRAY_LAUNCHER_NAME"
  {
    echo '#!/usr/bin/env bash'
    printf 'exec gjs -m %q "$@"\n' "$CURRENT_LINK/desktop-tray/main.js"
  } >"$tray_launcher"
  chmod +x "$tray_launcher"
fi

# Render in bash (no sed replacement): the path may contain '&', '|' or '\'. Check it before
# anything is switched.
template="$(<"$REPO_ROOT/packaging/systemd/$UNIT_NAME.in")"
rendered="${template%%@EXEC_START@*}$EXEC_START${template#*@EXEC_START@}"
if ! printf '%s\n' "$rendered" | grep -qFx "ExecStart=$EXEC_START"; then
  abort_before_switch "the rendered unit does not contain ExecStart=$EXEC_START"
fi

echo "==> Installing the systemd user unit and the D-Bus activation file"
# Atomic switch: a running service keeps using the real versioned path it started from.
point_current "versions/$version"
mkdir -p "$(dirname "$UNIT_PATH")" "$(dirname "$ACTIVATION_PATH")"
printf '%s\n' "$rendered" >"$UNIT_PATH"
cp "$REPO_ROOT/packaging/dbus/$BUS_NAME.service" "$ACTIVATION_PATH"

echo "==> Enabling and starting the service"
was_active=0
if unit_active; then was_active=1; fi
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
      none | idle)
        if systemctl --user restart "$UNIT_NAME"; then activated=1; else start_failed=1; fi ;;
      *)
        restart_skipped=1
        echo "warning: not restarting $UNIT_NAME because a recording is running or its state is unknown; the new version takes effect on the next restart or login." >&2 ;;
    esac
  else
    start_failed=1
  fi
else
  if systemctl --user enable --now "$UNIT_NAME"; then activated=1; else start_failed=1; fi
fi

# A new version that cannot start must not stay in charge: Restart=on-failure and D-Bus activation
# would keep launching it. Go back to what worked and keep the new directory for inspection.
rolled_back=""
if ((start_failed)) && [[ -n "$previous" || $legacy_tool == 1 ]]; then
  echo "==> The new version failed to start; rolling back" >&2
  if [[ -n "$previous" ]]; then
    point_current "$previous"
    rolled_back="${previous##*/}"
  else
    # Migration: the old uv tool is still installed; point the unit back at it.
    rm -f "$CURRENT_LINK"
    printf '%s\n' "$unit_backup" >"$UNIT_PATH"
    rolled_back="the legacy service ($legacy_exec)"
  fi
  systemctl --user daemon-reload || reload_failed=1
  if ((was_active)); then
    systemctl --user restart "$UNIT_NAME" || echo "warning: restarting the previous version failed too." >&2
  fi
fi

# The desktop entry follows the switch: it is written only for a version that stays in charge, and
# a rollback removes it when the restored version has no review launcher (a pre-F4 install).
if [[ -z "$rolled_back" ]]; then
  desktop_template="$(<"$REPO_ROOT/packaging/applications/$DESKTOP_ENTRY_NAME.in")"
  mkdir -p "$APPLICATIONS_DIR"
  desktop_exec="$(desktop_exec_argument "$CURRENT_LINK/bin/$REVIEW_LAUNCHER_NAME")"
  # Every placeholder (the entry and its actions); quoted so a "&" in the path stays literal.
  printf '%s\n' "${desktop_template//@EXEC@/"$desktop_exec"}" >"$DESKTOP_ENTRY_PATH"
  refresh_desktop_database
elif [[ ! -x "$CURRENT_LINK/bin/$REVIEW_LAUNCHER_NAME" ]]; then
  rm -f "$DESKTOP_ENTRY_PATH"
  refresh_desktop_database
fi

migration_note=""
if ((activated)) && unit_active; then
  # The service now runs the new version: older versions and the legacy uv tool are unused.
  for old in "$VERSIONS_DIR"/*; do
    if [[ -d "$old" && "$old" != "$version_dir" ]]; then rm -rf "$old"; fi
  done
  if ((legacy_tool)); then
    uv tool uninstall meeting-scribe || migration_note="old uv tool could not be removed (uv tool uninstall meeting-scribe)"
  fi
elif ((legacy_tool)) && [[ -z "$rolled_back" ]]; then
  migration_note="the old uv tool is kept while the old service may still run; remove it later with: uv tool uninstall meeting-scribe"
fi

# Offline and bounded: never a download. The version in charge (after a rollback, the previous
# one) answers; a missing or failing check is reported as pending, which is not a failure.
readonly MODELS_CHECK_TIMEOUT_DEFAULT=30
models_check_timeout="$(pf_seconds "${MEETING_SCRIBE_MODELS_CHECK_TIMEOUT:-}" "$MODELS_CHECK_TIMEOUT_DEFAULT")"
models_ready=0
if [[ -x "$CURRENT_LINK/bin/meeting-scribe" ]] \
  && timeout --kill-after=5 "$models_check_timeout" "$CURRENT_LINK/bin/meeting-scribe" \
    prefetch-models --check >/dev/null 2>&1; then
  models_ready=1
fi

# The desktop client: the GNOME extension on GNOME Shell, the tray anywhere else.
extension_failed=""
tray_failed=""
if [[ "$PF_DESKTOP" == tray ]]; then
  # The autostart entry and the icons follow the version in charge (after a rollback, the previous
  # one); a restored version without a tray loses the entry, as the review app's entry does. Then
  # the running tray is replaced: stopped by its PID, the new one started detached.
  echo "==> Installing the tray autostart entry and icons, and restarting the tray"
  if [[ ! -x "$CURRENT_LINK/bin/$TRAY_LAUNCHER_NAME" ]]; then
    rm -f "$TRAY_AUTOSTART_PATH"
    tray_failed="the version in charge has no tray launcher"
  else
    autostart_template="$(<"$REPO_ROOT/packaging/autostart/$TRAY_AUTOSTART_NAME.in")"
    autostart_exec="$(desktop_exec_argument "$CURRENT_LINK/bin/$TRAY_LAUNCHER_NAME")"
    if ! mkdir -p "$(dirname "$TRAY_AUTOSTART_PATH")" "$TRAY_ICONS_DIR" \
      || ! printf '%s\n' "${autostart_template//@EXEC@/"$autostart_exec"}" >"$TRAY_AUTOSTART_PATH" \
      || ! cp "$CURRENT_LINK/desktop-tray/icons/"*.svg "$TRAY_ICONS_DIR/"; then
      tray_failed="the autostart entry or the icons could not be written"
    elif ! stop_tray || ! start_tray; then
      tray_failed="$TRAY_ERROR"
    fi
  fi
else
  echo "==> Installing the GNOME extension"
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
fi

echo
echo "meeting-scribe installed"
if [[ -n "$rolled_back" ]]; then
  echo "  version:   $rolled_back (rolled back; $version failed to start)"
  echo "  rollback:  new version failed to start; rolled back to $rolled_back; the new version is kept in versions/$version for inspection"
  echo "             Details: journalctl --user -u $UNIT_NAME"
else
  echo "  version:   $version"
fi
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
if [[ "$PF_DESKTOP" == tray ]]; then
  if [[ -n "$tray_failed" ]]; then
    echo "  tray: FAILED ($tray_failed)"
  else
    echo "  tray: running"
  fi
elif [[ -n "$extension_failed" ]]; then
  echo "  extension: FAILED ($extension_failed)"
else
  echo "  extension: $extension_state ($EXTENSION_UUID)"
fi
echo "  review app: ready ($DESKTOP_ENTRY_NAME)"
if ((models_ready)); then
  echo "  models:    ready (cached)"
else
  echo "  models:    PENDING: add your Hugging Face token in Preferences and press Download models; recording works meanwhile"
fi
# This is the result of the preflight taken at the start of the run, not a new probe.
echo "  ollama:    reachable at preflight; model $PF_OLLAMA_MODEL present"
echo "  config:    open Preferences from the Meeting Scribe menu (saved in $CONFIG_HOME/meeting-scribe/settings.json)"
echo "             .env in the same folder stays an optional developer override (see .env.example)"
if ((start_failed || reload_failed)) || [[ -n "$extension_failed" || -n "$tray_failed" ]]; then
  exit 1
fi
