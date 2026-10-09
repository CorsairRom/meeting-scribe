import re

import pytest

from meeting_scribe.base.domain.errors import InvalidStateTransitionError, MeetingScribeError
from meeting_scribe.recording.domain.errors import (
    AudioCaptureError,
    AudioDeviceNotFoundError,
    NoActiveRecordingError,
    RecordingAlreadyActiveError,
    RecordingStorageError,
)


def _all_subclasses(cls: type[MeetingScribeError]) -> list[type[MeetingScribeError]]:
    found: list[type[MeetingScribeError]] = []
    for sub in cls.__subclasses__():
        found.append(sub)
        found.extend(_all_subclasses(sub))
    return found


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (RecordingAlreadyActiveError, "RECORDING_ALREADY_ACTIVE"),
        (NoActiveRecordingError, "NO_ACTIVE_RECORDING"),
        (AudioDeviceNotFoundError, "AUDIO_DEVICE_NOT_FOUND"),
        (AudioCaptureError, "AUDIO_CAPTURE_FAILED"),
        (RecordingStorageError, "RECORDING_STORAGE_FAILED"),
    ],
)
def test_recording_error_codes_are_stable(error: type[MeetingScribeError], code: str) -> None:
    assert error.error_code == code
    assert issubclass(error, MeetingScribeError)


def test_recording_errors_stay_unique_with_the_whole_hierarchy() -> None:
    codes = [cls.error_code for cls in [MeetingScribeError, *_all_subclasses(MeetingScribeError)]]

    assert all(re.fullmatch(r"[A-Z][A-Z0-9]*(_[A-Z0-9]+)*", code) for code in codes)
    assert len(codes) == len(set(codes))


def test_state_conflicts_are_invalid_state_transitions() -> None:
    assert issubclass(RecordingAlreadyActiveError, InvalidStateTransitionError)
    assert issubclass(NoActiveRecordingError, InvalidStateTransitionError)
