"""`--meeting ID` names the meeting to open; no argument means the newest (AC-13)."""

from meeting_scribe_review.core.launch import (
    meeting_id_from_options,
    show_in_running_window,
    wants_preferences,
)


def test_a_meeting_option_gives_its_id() -> None:
    assert meeting_id_from_options({"meeting": "2026-10-09_101500"}) == "2026-10-09_101500"


def test_no_option_means_no_preference() -> None:
    assert meeting_id_from_options({}) == ""


def test_the_id_is_trimmed() -> None:
    assert meeting_id_from_options({"meeting": "  m1\n"}) == "m1"


def test_a_value_that_is_not_text_is_ignored() -> None:
    assert meeting_id_from_options({"meeting": 7}) == ""
    assert meeting_id_from_options({"meeting": None}) == ""


def test_the_preferences_flag_asks_for_the_dialog() -> None:
    assert wants_preferences({"preferences": True}) is True
    assert wants_preferences({}) is False
    assert wants_preferences({"preferences": "yes"}) is False


class _Window:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def open_meeting(self, meeting_id: str) -> None:
        self.calls.append(("open_meeting", meeting_id))

    def present(self) -> None:
        self.calls.append(("present", ""))

    def open_preferences(self) -> None:
        self.calls.append(("open_preferences", ""))


def test_preferences_against_a_running_window_keeps_the_meeting_under_review() -> None:
    window = _Window()
    show_in_running_window(window, {"preferences": True})
    assert window.calls == [("present", ""), ("open_preferences", "")]


def test_a_meeting_option_still_switches_the_meeting_before_the_dialog() -> None:
    window = _Window()
    show_in_running_window(window, {"meeting": "m1", "preferences": True})
    assert window.calls == [
        ("open_meeting", "m1"),
        ("present", ""),
        ("open_preferences", ""),
    ]


def test_a_plain_second_launch_reloads_the_newest_meeting() -> None:
    window = _Window()
    show_in_running_window(window, {})
    assert window.calls == [("open_meeting", ""), ("present", "")]
