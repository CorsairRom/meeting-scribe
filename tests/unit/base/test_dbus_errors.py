from dbus_fast import DBusError

from meeting_scribe.base.domain.errors import EntityNotFoundError, MeetingScribeError
from meeting_scribe.base.infrastructure.dbus.constants import (
    BUS_NAME,
    ERROR_PREFIX,
    OBJECT_PATH,
)
from meeting_scribe.base.infrastructure.dbus.errors import to_dbus_error
from meeting_scribe.recording.domain.errors import RecordingAlreadyActiveError


def test_constants_follow_the_published_names() -> None:
    assert BUS_NAME == "dev.meetingscribe.Service"
    assert OBJECT_PATH == "/dev/meetingscribe/Service"
    assert ERROR_PREFIX == "dev.meetingscribe.Error"


def test_to_dbus_error_embeds_the_error_code_and_message() -> None:
    error = to_dbus_error(RecordingAlreadyActiveError("Recording 'x' is already active."))

    assert isinstance(error, DBusError)
    assert error.type == "dev.meetingscribe.Error.RECORDING_ALREADY_ACTIVE"
    assert error.text == "Recording 'x' is already active."


def test_to_dbus_error_uses_the_instance_code_with_detail_code() -> None:
    error = to_dbus_error(EntityNotFoundError("Meeting 'a' does not exist.", detail_code="MEETING"))

    assert error.type == "dev.meetingscribe.Error.ENTITY_NOT_FOUND_MEETING"


def test_to_dbus_error_falls_back_to_internal_error_for_the_base_class() -> None:
    error = to_dbus_error(MeetingScribeError("boom"))

    assert error.type == "dev.meetingscribe.Error.INTERNAL_ERROR"
    assert error.text == "boom"
