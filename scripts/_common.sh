# Shared helpers for install.sh and uninstall.sh (sourced, not executed).
# External commands come from PATH so tests can substitute fakes.

BUS_NAME="dev.meetingscribe.Service"
UNIT_NAME="meeting-scribe.service"
EXTENSION_UUID="meeting-scribe@meetingscribe.dev"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
UNIT_PATH="$CONFIG_HOME/systemd/user/$UNIT_NAME"
ACTIVATION_PATH="$DATA_HOME/dbus-1/services/$BUS_NAME.service"
# Every install lives in its own versions/<id>/ directory (uv tool dir and bin dir); `current`
# is a symlink to the active one and the unit's ExecStart always uses the stable current/ path.
INSTALL_ROOT="$DATA_HOME/meeting-scribe"
VERSIONS_DIR="$INSTALL_ROOT/versions"
CURRENT_LINK="$INSTALL_ROOT/current"
EXEC_START="$CURRENT_LINK/bin/meeting-scribe"

# The review app (GTK4 + libadwaita) runs on the system Python, never on the uv tool's Python:
# PyGObject comes from the distribution. REVIEW_APP_PYTHON exists so tests can substitute a fake.
REVIEW_APP_PYTHON="${REVIEW_APP_PYTHON:-/usr/bin/python3}"
APPLICATIONS_DIR="$DATA_HOME/applications"
DESKTOP_ENTRY_NAME="dev.meetingscribe.Review.desktop"
DESKTOP_ENTRY_PATH="$APPLICATIONS_DIR/$DESKTOP_ENTRY_NAME"
REVIEW_LAUNCHER_NAME="meeting-scribe-review"

# Prints $1 as one Exec argument of a desktop entry: double-quoted, with `"`, a backtick, `$` and
# `\` escaped for the quoting level, `%` doubled (field codes), and every `\` doubled again for the
# string level of the file (Desktop Entry Specification, "The Exec key").
desktop_exec_argument() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//\`/\\\`}"
  value="${value//\$/\\\$}"
  value="${value//%/%%}"
  value="${value//\\/\\\\}"
  printf '"%s"' "$value"
}

# Best effort: refresh the desktop entry cache when the tool and the directory exist.
refresh_desktop_database() {
  if [[ -d "$APPLICATIONS_DIR" ]] && command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPLICATIONS_DIR" >/dev/null 2>&1 || true
  fi
}

# Seconds before a bus call is abandoned (a hung service must not hang the installer).
GDBUS_TIMEOUT=5

# Prints exactly one of: none (no service owns the bus name), idle (the service reports IDLE,
# STOPPED or FAILED: safe to replace), recording, unknown (the state could not be read, parsed or
# is any value not listed here: fail closed).
service_state() {
  local owner state
  owner="$(gdbus call --session --timeout "$GDBUS_TIMEOUT" --dest org.freedesktop.DBus \
    --object-path /org/freedesktop/DBus --method org.freedesktop.DBus.NameHasOwner \
    "$BUS_NAME" 2>/dev/null)" || { echo unknown; return 0; }
  case "$owner" in
    *false*) echo none; return 0 ;;
    *true*) ;;
    *) echo unknown; return 0 ;;
  esac
  state="$(gdbus call --session --timeout "$GDBUS_TIMEOUT" --dest "$BUS_NAME" \
    --object-path /dev/meetingscribe/Service --method org.freedesktop.DBus.Properties.Get \
    dev.meetingscribe.Recording1 State 2>/dev/null)" || { echo unknown; return 0; }
  case "$state" in
    *RECORDING*) echo recording ;;
    *"<'IDLE'>"* | *"<'STOPPED'>"* | *"<'FAILED'>"*) echo idle ;;
    *) echo unknown ;;
  esac
}

# Refuses (returns 1 with a message) while a recording runs or its state is unknown.
# $1 is the follow-up the user should run.
# $2 is what FORCE=1 may override: "unknown" (the state could not be read; a positively detected
#    recording is never overridden) or "recording" (the caller is destructive by intent, such as
#    uninstall, and accepts stopping a recording).
# $3 is an optional hint appended to the refusal messages.
refuse_unless_safe() {
  local force_scope="$2" hint="${3:-}"
  case "$(service_state)" in
    recording)
      if [[ "${FORCE:-0}" == 1 && "$force_scope" == recording ]]; then
        echo "warning: a recording is in progress; continuing because FORCE=1." >&2
        return 0
      fi
      echo "error: a recording is in progress; stop it from the top-bar menu, then run $1 again.${hint:+ $hint}" >&2
      return 1 ;;
    unknown)
      if [[ "${FORCE:-0}" == 1 ]]; then
        echo "warning: the recording state could not be read; continuing because FORCE=1." >&2
        return 0
      fi
      echo "error: the recording state could not be read (or is not recognised), so a recording might be running. Stop any recording from the top-bar menu, then run 'systemctl --user stop $UNIT_NAME' or run $1 again with FORCE=1.${hint:+ $hint}" >&2
      return 1 ;;
  esac
}

unit_active() {
  systemctl --user is-active --quiet "$UNIT_NAME"
}
