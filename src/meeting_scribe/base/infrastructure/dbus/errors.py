"""Translate domain errors into D-Bus errors."""

from dbus_fast import DBusError

from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.base.infrastructure.dbus.constants import ERROR_PREFIX


def to_dbus_error(error: MeetingScribeError) -> DBusError:
    """``dev.meetingscribe.Error.<error_code>`` carrying the error message only (no traceback)."""
    return DBusError(f"{ERROR_PREFIX}.{error.error_code}", str(error))
