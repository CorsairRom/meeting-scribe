import pytest

from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.preferences.domain.errors import (
    SettingsFileInvalidError,
    SettingsStorageFailedError,
)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (SettingsStorageFailedError, "SETTINGS_STORAGE_FAILED"),
        (SettingsFileInvalidError, "SETTINGS_FILE_INVALID"),
    ],
)
def test_stable_codes(error: type[MeetingScribeError], code: str) -> None:
    assert error.error_code == code
    assert issubclass(error, MeetingScribeError)
