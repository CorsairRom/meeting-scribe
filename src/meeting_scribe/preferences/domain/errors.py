"""Preferences errors with stable codes (registered in ``error-codes-catalog.md``)."""

from meeting_scribe.base.domain.errors import MeetingScribeError


class SettingsStorageFailedError(MeetingScribeError):
    """The settings file could not be written; nothing changed."""

    error_code = "SETTINGS_STORAGE_FAILED"


class SettingsFileInvalidError(MeetingScribeError):
    """The settings file could not be read; it is ignored until the next save replaces it."""

    error_code = "SETTINGS_FILE_INVALID"
