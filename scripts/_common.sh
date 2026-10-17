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
# PyGObject comes from the distribution. REVIEW_APP_PYTHON is an interpreter override: tests
# substitute a fake, and `make run-review-app` uses it too.
REVIEW_APP_PYTHON="${REVIEW_APP_PYTHON:-/usr/bin/python3}"
APPLICATIONS_DIR="$DATA_HOME/applications"
DESKTOP_ENTRY_NAME="dev.meetingscribe.Review.desktop"
DESKTOP_ENTRY_PATH="$APPLICATIONS_DIR/$DESKTOP_ENTRY_NAME"
REVIEW_LAUNCHER_NAME="meeting-scribe-review"

# The tray outside GNOME Shell (docs/specs/desktop-tray.md sections 1, 2 and 7): its files live in
# each version directory, its launcher in the version's bin/, and an autostart entry starts it at
# every login. It owns TRAY_BUS_NAME while it runs; its icons are named after that bus name.
TRAY_BUS_NAME="dev.meetingscribe.Tray"
TRAY_LAUNCHER_NAME="meeting-scribe-tray"
TRAY_AUTOSTART_NAME="$TRAY_BUS_NAME.desktop"
TRAY_AUTOSTART_PATH="$CONFIG_HOME/autostart/$TRAY_AUTOSTART_NAME"
TRAY_ICONS_DIR="$DATA_HOME/icons/hicolor/scalable/status"
# How long the tray name may take to go after a stop, or to come after a start: 5 s, or the
# positive whole number of seconds in MEETING_SCRIBE_TRAY_WAIT_SECONDS (tests shorten it).
TRAY_WAIT_SECONDS=5
if [[ "${MEETING_SCRIBE_TRAY_WAIT_SECONDS:-}" =~ ^[1-9][0-9]*$ ]]; then
  TRAY_WAIT_SECONDS="$MEETING_SCRIBE_TRAY_WAIT_SECONDS"
fi
TRAY_POLL_SECONDS=0.2 # the pause between two questions about the tray name

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

# bus_name_owner NAME: prints yes, no or unknown (no gdbus, no answer, or an unreadable one). One
# read-only question to the bus daemon, bounded by GDBUS_TIMEOUT; never a call to a service.
bus_name_owner() {
  local answer
  if ! command -v gdbus >/dev/null 2>&1; then
    echo unknown
    return 0
  fi
  answer="$(gdbus call --session --timeout "$GDBUS_TIMEOUT" --dest org.freedesktop.DBus \
    --object-path /org/freedesktop/DBus --method org.freedesktop.DBus.NameHasOwner "$1" \
    2>/dev/null)" || {
    echo unknown
    return 0
  }
  case "$answer" in
    *true*) echo yes ;;
    *false*) echo no ;;
    *) echo unknown ;;
  esac
}

# -- the tray -------------------------------------------------------------------------------------

# Why the last stop_tray or start_tray call failed, for the summary or a warning.
TRAY_ERROR=""

# tray_wait_owner yes|no: asks every TRAY_POLL_SECONDS until the tray name's owner state is $1.
# The wait is bounded by elapsed time, not by a count of questions, so a slow bus (each question
# may take up to GDBUS_TIMEOUT) does not stretch it. Bash's SECONDS counts whole seconds, hence
# the extra second: the wait lasts at least TRAY_WAIT_SECONDS and less than one second more, plus
# the last question.
tray_wait_owner() {
  local deadline=$((SECONDS + TRAY_WAIT_SECONDS + 1))
  while true; do
    if [[ "$(bus_name_owner "$TRAY_BUS_NAME")" == "$1" ]]; then return 0; fi
    if ((SECONDS >= deadline)); then return 1; fi
    sleep "$TRAY_POLL_SECONDS"
  done
}

# Stops the running tray, if any: its PID comes from the bus daemon (a read-only
# GetConnectionUnixProcessID for TRAY_BUS_NAME), it gets SIGTERM (never pkill -f), and the name
# must go within TRAY_WAIT_SECONDS. Returns 1 with TRAY_ERROR set when it could not be stopped.
stop_tray() {
  local answer pid
  case "$(bus_name_owner "$TRAY_BUS_NAME")" in
    no) return 0 ;;
    unknown)
      TRAY_ERROR="the session bus did not say whether $TRAY_BUS_NAME is running"
      return 1 ;;
  esac
  answer="$(gdbus call --session --timeout "$GDBUS_TIMEOUT" --dest org.freedesktop.DBus \
    --object-path /org/freedesktop/DBus --method org.freedesktop.DBus.GetConnectionUnixProcessID \
    "$TRAY_BUS_NAME" 2>/dev/null)" || answer=""
  # A PID at or below 1 is refused: `kill 0` would signal this script's whole process group, and
  # PID 1 is init.
  if [[ ! "$answer" =~ ^\(uint32\ ([0-9]+),\)$ ]] || ((BASH_REMATCH[1] <= 1)); then
    # The name may have gone between the two questions: nothing is left to stop then.
    if [[ "$(bus_name_owner "$TRAY_BUS_NAME")" == no ]]; then return 0; fi
    TRAY_ERROR="the PID of the running $TRAY_BUS_NAME could not be read"
    return 1
  fi
  pid="${BASH_REMATCH[1]}"
  kill "$pid" 2>/dev/null || true # it may have exited on its own meanwhile
  if ! tray_wait_owner no; then
    TRAY_ERROR="the running $TRAY_BUS_NAME (pid $pid) did not stop within $TRAY_WAIT_SECONDS s"
    return 1
  fi
}

# Starts the tray of the version in charge, detached from this script and its terminal, and waits
# for it to own TRAY_BUS_NAME. Returns 1 with TRAY_ERROR set when it does not.
start_tray() {
  local launcher="$CURRENT_LINK/bin/$TRAY_LAUNCHER_NAME"
  if [[ ! -x "$launcher" ]]; then
    TRAY_ERROR="the version in charge has no tray launcher ($launcher)"
    return 1
  fi
  if ! (cd / && setsid -f "$launcher" </dev/null >/dev/null 2>&1); then
    TRAY_ERROR="the tray could not be started (setsid -f $launcher)"
    return 1
  fi
  if ! tray_wait_owner yes; then
    TRAY_ERROR="the tray did not take $TRAY_BUS_NAME within $TRAY_WAIT_SECONDS s; run $launcher in a terminal to see why"
    return 1
  fi
}

# Removes the tray's autostart entry and its icons (the files named after its bus name), when
# present; prints a step line only when there is something to remove.
remove_tray_files() {
  local icons=()
  shopt -s nullglob
  icons=("$TRAY_ICONS_DIR/$TRAY_BUS_NAME"-*.svg)
  shopt -u nullglob
  if [[ ! -e "$TRAY_AUTOSTART_PATH" && ${#icons[@]} -eq 0 ]]; then return 0; fi
  echo "==> Removing the tray autostart entry and icons"
  rm -f "$TRAY_AUTOSTART_PATH" "${icons[@]}"
}
