# Shared helpers for install.sh and uninstall.sh (sourced, not executed).
# External commands come from PATH so tests can substitute fakes.

BUS_NAME="dev.meetingscribe.Service"
UNIT_NAME="meeting-scribe.service"
EXTENSION_UUID="meeting-scribe@meetingscribe.dev"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
UNIT_PATH="$CONFIG_HOME/systemd/user/$UNIT_NAME"
ACTIVATION_PATH="$DATA_HOME/dbus-1/services/$BUS_NAME.service"

# Prints the Recording1 State when the service owns its bus name, nothing otherwise.
service_state() {
  local owner
  owner="$(gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
    --method org.freedesktop.DBus.NameHasOwner "$BUS_NAME" 2>/dev/null)" || return 0
  case "$owner" in *true*) ;; *) return 0 ;; esac
  gdbus call --session --dest "$BUS_NAME" --object-path /dev/meetingscribe/Service \
    --method org.freedesktop.DBus.Properties.Get dev.meetingscribe.Recording1 State \
    2>/dev/null || true
}

is_recording() {
  [[ "$(service_state)" == *RECORDING* ]]
}

unit_active() {
  systemctl --user is-active --quiet "$UNIT_NAME"
}
