#!/usr/bin/env bash
# One-command install: service (uv tool), systemd --user unit, D-Bus activation, GNOME extension.
# Idempotent; safe to re-run to upgrade. Refuses to run while a recording is in progress, or its
# state is unknown unless FORCE=1 (FORCE never overrides a recording).
#
# Every run installs into a fresh versions/<id>/ directory, verifies it, and only then repoints the
# `current` symlink atomically, so the environment a running service was loaded from is never
# replaced. Old versions are pruned only once the service runs the new one. If the new version
# fails to start, `current` (or, on a migration, the unit) is restored to what worked before.
#
# The ML models are downloaded as a visible step AFTER the switch (OQ-7): recording never needs
# them, so a missing network or Hugging Face login must not block installing the service. A failed
# download is reported in the summary (the worker runs offline and says MODEL_UNAVAILABLE until a
# later `make install` fetches them); it never fails the install. The download is bounded by
# MEETING_SCRIBE_PREFETCH_TIMEOUT seconds (default 3600) and skipped when the install rolled back.
#
# Ollama and the local summary model are only CHECKED, read-only (`ollama list`, bounded by
# MEETING_SCRIBE_OLLAMA_TIMEOUT seconds, default 10): the install never starts Ollama, never pulls a
# model and never sends a prompt. A gap only warns with the command to fix it.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/_common.sh
source "$REPO_ROOT/scripts/_common.sh"

# Decide before touching anything.
refuse_unless_safe "'make install'" unknown || exit 1

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
  printf '%s\n' "${desktop_template%%@EXEC@*}$(desktop_exec_argument "$CURRENT_LINK/bin/$REVIEW_LAUNCHER_NAME")${desktop_template#*@EXEC@}" >"$DESKTOP_ENTRY_PATH"
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

# Bounded (MEETING_SCRIBE_PREFETCH_TIMEOUT seconds, default 3600) so a stalled download cannot hang
# the install. Skipped after a rollback: the new version failed to start, which is the real problem.
readonly PREFETCH_TIMEOUT_DEFAULT=3600
readonly EXIT_TIMED_OUT=124 # timeout(1): the command ran past the limit (SIGTERM)
readonly EXIT_KILLED=137    # timeout --kill-after: SIGTERM was ignored, so it was SIGKILLed (128+9)
prefetch_timeout="${MEETING_SCRIBE_PREFETCH_TIMEOUT:-$PREFETCH_TIMEOUT_DEFAULT}"
if ! [[ "$prefetch_timeout" =~ ^[1-9][0-9]*$ ]]; then
  echo "warning: MEETING_SCRIBE_PREFETCH_TIMEOUT='$prefetch_timeout' is not a positive integer of seconds; using $PREFETCH_TIMEOUT_DEFAULT." >&2
  prefetch_timeout="$PREFETCH_TIMEOUT_DEFAULT"
fi
prefetch_cmd="$CURRENT_LINK/bin/meeting-scribe prefetch-models"
models_state="downloaded (Hugging Face cache; processing runs offline)"
models_status=ok # ok | failed | timed_out | skipped_rollback
if [[ -n "$rolled_back" ]]; then
  models_status=skipped_rollback
else
  echo "==> Downloading the speech models (Hugging Face cache; needs the network and 'hf auth login')"
  prefetch_status=0
  timeout --kill-after=10 "$prefetch_timeout" "$version_dir/bin/meeting-scribe" prefetch-models \
    || prefetch_status=$?
  case "$prefetch_status" in
    0) ;;
    "$EXIT_TIMED_OUT" | "$EXIT_KILLED") models_status=timed_out ;;
    *) models_status=failed ;;
  esac
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

# Read-only check of the system toolkit; a gap only warns (like the extension and the models).
review_missing=()
if ! "$REVIEW_APP_PYTHON" -c 'import gi' >/dev/null 2>&1; then
  review_missing=(python3-gobject gtk4 libadwaita)
else
  "$REVIEW_APP_PYTHON" -c 'import gi; gi.require_version("Gtk", "4.0")' >/dev/null 2>&1 \
    || review_missing+=(gtk4)
  "$REVIEW_APP_PYTHON" -c 'import gi; gi.require_version("Adw", "1")' >/dev/null 2>&1 \
    || review_missing+=(libadwaita)
fi

# The value of a `KEY=value` line: CR removed, surrounding spaces trimmed; a quoted value ends at its
# closing quote (so a trailing comment is dropped); an unquoted one ends at a ` #` comment.
parse_env_value() {
  local value="${1%$'\r'}" quote
  value="${value#"${value%%[![:space:]]*}"}"
  case "$value" in
    \"* | \'*)
      quote="${value:0:1}"
      value="${value:1}"
      value="${value%%"$quote"*}" ;;
    *) value="${value%%[[:space:]]\#*}" ;;
  esac
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

# The configured local model: SUMMARIES_LOCAL_MODEL from the environment, else that one line of the
# settings file (nothing else in it is read), else the default. Quotes and spaces are stripped.
readonly OLLAMA_MODEL_DEFAULT="qwen3.5:9b"
readonly OLLAMA_TIMEOUT_DEFAULT=10
ollama_model="${SUMMARIES_LOCAL_MODEL:-}"
if [[ -z "$ollama_model" && -f "$CONFIG_HOME/meeting-scribe/.env" ]]; then
  ollama_model="$(grep -m1 '^SUMMARIES_LOCAL_MODEL=' "$CONFIG_HOME/meeting-scribe/.env" 2>/dev/null || true)"
  ollama_model="$(parse_env_value "${ollama_model#SUMMARIES_LOCAL_MODEL=}")"
fi
ollama_model="${ollama_model:-$OLLAMA_MODEL_DEFAULT}"
ollama_timeout="${MEETING_SCRIBE_OLLAMA_TIMEOUT:-$OLLAMA_TIMEOUT_DEFAULT}"
if ! [[ "$ollama_timeout" =~ ^[1-9][0-9]*$ ]]; then ollama_timeout="$OLLAMA_TIMEOUT_DEFAULT"; fi
ollama_status=missing # ok | missing | unreachable | no_model
if command -v ollama >/dev/null 2>&1; then
  if ollama_list="$(timeout --kill-after=2 "$ollama_timeout" ollama list 2>/dev/null)"; then
    ollama_status=no_model
    while read -r listed _; do
      if [[ "$listed" == "$ollama_model" || "$listed" == "$ollama_model:latest" ]]; then
        ollama_status=ok
        break
      fi
    done <<<"$ollama_list"
  else
    ollama_status=unreachable
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
if [[ -n "$extension_failed" ]]; then
  echo "  extension: FAILED ($extension_failed)"
else
  echo "  extension: $extension_state ($EXTENSION_UUID)"
fi
if ((${#review_missing[@]})); then
  echo "  review app: NOT usable (missing: ${review_missing[*]}); install with: sudo dnf install ${review_missing[*]}"
  echo "             Sample playback also needs gstreamer1-plugins-good. The extension menu item stays; re-run 'make install' to re-check."
else
  echo "  review app: ready ($DESKTOP_ENTRY_NAME)"
fi
case "$models_status" in
  skipped_rollback)
    echo "  models:    NOT downloaded, the install rolled back (the start failure is the real problem)."
    echo "             Fix it, run 'make install' again, or fetch them later with: $prefetch_cmd"
    ;;
  timed_out)
    echo "  models:    NOT downloaded, the download timed out after $prefetch_timeout s; recording works,"
    echo "             processing will fail with MODEL_UNAVAILABLE. Retry with: $prefetch_cmd"
    echo "             (MEETING_SCRIBE_PREFETCH_TIMEOUT sets the limit in seconds)."
    ;;
  failed)
    echo "  models:    NOT downloaded; recording works, processing will fail with MODEL_UNAVAILABLE."
    echo "             See the download output above for the cause (for a gated model: accept its terms"
    echo "             and run 'hf auth login' once). Then retry with: $prefetch_cmd"
    ;;
  *)
    echo "  models:    $models_state"
    ;;
esac
case "$ollama_status" in
  ok)
    echo "  ollama:    reachable; model $ollama_model present"
    ;;
  no_model)
    echo "  ollama:    reachable; model $ollama_model NOT found (summaries stay unavailable until it is pulled)"
    echo "             Pull it with: ollama pull $ollama_model"
    ;;
  unreachable)
    echo "  ollama:    found but not reachable (summaries stay unavailable; recording and processing are unaffected)"
    echo "             Start it with: systemctl start ollama (or run: ollama serve), then re-run 'make install' to re-check."
    ;;
  *)
    echo "  ollama:    NOT found (summaries stay unavailable; recording and processing are unaffected)"
    echo "             Install it from https://ollama.com/download, then run: ollama pull $ollama_model"
    ;;
esac
echo "  config:    $CONFIG_HOME/meeting-scribe/.env (optional, see .env.example)"
if ((start_failed || reload_failed)) || [[ -n "$extension_failed" ]]; then
  exit 1
fi
