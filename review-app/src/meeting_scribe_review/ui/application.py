"""``Adw.Application``: a single instance; ``--meeting ID`` and ``--preferences`` (spec section 8).

The application id gives the app its unique bus name, so a second launch activates the first
instance, and sets the Wayland ``app_id`` that GNOME Shell matches to the desktop entry. The window
is presented only from a launch; data arriving later never steals focus.
"""

import logging
import sys

from gi.repository import Adw, Gio, GLib

from meeting_scribe_review.core.launch import (
    MEETING_OPTION,
    PREFERENCES_OPTION,
    meeting_id_from_options,
    show_in_running_window,
    wants_preferences,
)
from meeting_scribe_review.ui.dbus_gateway import DBusGateway
from meeting_scribe_review.ui.key_store import SecretKeyStore
from meeting_scribe_review.ui.media_player import MediaPlayer
from meeting_scribe_review.ui.window import ReviewWindow

APP_ID = "dev.meetingscribe.Review"


class ReviewApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.add_main_option(
            MEETING_OPTION,
            0,
            GLib.OptionFlags.NONE,
            GLib.OptionArg.STRING,
            "Open this meeting instead of the newest reviewed one",
            "ID",
        )
        self.add_main_option(
            PREFERENCES_OPTION,
            0,
            GLib.OptionFlags.NONE,
            GLib.OptionArg.NONE,
            "Open the Preferences dialog",
            None,
        )
        self.set_accels_for_action("win.preferences", ["<Control>comma"])
        self._window: ReviewWindow | None = None

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        options = command_line.get_options_dict().end().unpack()
        meeting_id = meeting_id_from_options(options)
        if self._window is None:
            self._window = ReviewWindow(
                self, DBusGateway(), MediaPlayer(), SecretKeyStore(), meeting_id
            )
            self._window.connect("destroy", self._forget_window)
            self._window.present()
            if wants_preferences(options):
                self._window.open_preferences()  # always over the review window (A-2)
        else:
            show_in_running_window(self._window, options)
        return 0

    def _forget_window(self, _window: ReviewWindow) -> None:
        self._window = None


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(name)s: %(message)s")
    return ReviewApplication().run(sys.argv)
