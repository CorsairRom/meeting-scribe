"""Command-line options of the app, as plain values (the ``ui`` package unpacks the GLib dict)."""

from collections.abc import Mapping
from typing import Any, Protocol

MEETING_OPTION = "meeting"
PREFERENCES_OPTION = "preferences"


def meeting_id_from_options(options: Mapping[str, Any]) -> str:
    """The meeting named by ``--meeting ID``; empty means "the newest reviewed one" (AC-13)."""
    value = options.get(MEETING_OPTION)
    return value.strip() if isinstance(value, str) else ""


def wants_preferences(options: Mapping[str, Any]) -> bool:
    """True when ``--preferences`` was given: the window opens with the Preferences dialog."""
    return options.get(PREFERENCES_OPTION) is True


class RunningWindow(Protocol):
    def open_meeting(self, meeting_id: str) -> None: ...

    def present(self) -> None: ...

    def open_preferences(self) -> None: ...


def show_in_running_window(window: RunningWindow, options: Mapping[str, Any]) -> None:
    """A second launch: ``--preferences`` alone leaves the meeting under review untouched."""
    meeting_id = meeting_id_from_options(options)
    if meeting_id or not wants_preferences(options):
        window.open_meeting(meeting_id)
    window.present()
    if wants_preferences(options):
        window.open_preferences()
