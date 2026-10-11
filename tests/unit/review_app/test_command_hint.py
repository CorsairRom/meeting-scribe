"""The "Run: <command>" line shown under a service error."""

from meeting_scribe_review.core.errors import command_hint


def test_a_hint_is_worded_as_a_command_to_run() -> None:
    assert command_hint("systemctl --user start meeting-scribe.service") == (
        "Run: systemctl --user start meeting-scribe.service"
    )


def test_no_hint_gives_no_line() -> None:
    assert command_hint("") == ""
