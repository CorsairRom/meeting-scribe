# Shared helpers for install.sh and uninstall.sh (sourced, not executed).
# External commands come from PATH so tests can substitute fakes.

BUS_NAME="dev.meetingscribe.Service"
UNIT_NAME="meeting-scribe.service"
EXTENSION_UUID="meeting-scribe@meetingscribe.dev"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
UNIT_PATH="$CONFIG_HOME/systemd/user/$UNIT_NAME"
ACTIVATION_PATH="$DATA_HOME/dbus-1/services/$BUS_NAME.service"

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
# FORCE=1 overrides only the unknown state, never a positively detected recording.
# $1 is the follow-up the user should run, $2 an optional override hint.
refuse_unless_safe() {
  case "$(service_state)" in
    recording)
      echo "error: a recording is in progress; stop it from the top-bar menu, then run $1 again.${2:+ $2}" >&2
      return 1 ;;
    unknown)
      if [[ "${FORCE:-0}" == 1 ]]; then
        echo "warning: the recording state could not be read; continuing because FORCE=1." >&2
        return 0
      fi
      echo "error: the recording state could not be read (or is not recognised), so a recording might be running. Stop any recording from the top-bar menu, then run 'systemctl --user stop $UNIT_NAME' or run $1 again with FORCE=1.${2:+ $2}" >&2
      return 1 ;;
  esac
}

unit_active() {
  systemctl --user is-active --quiet "$UNIT_NAME"
}
