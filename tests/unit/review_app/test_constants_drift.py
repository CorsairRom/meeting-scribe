"""The app duplicates the D-Bus names on purpose (it cannot import the service): no drift."""

from meeting_scribe.base.infrastructure.dbus import constants as service
from meeting_scribe.meetings.infrastructure.dbus import meetings_interface, summaries_interface
from meeting_scribe_review.core import constants as app


def test_bus_name_object_path_and_error_prefix_match_the_service() -> None:
    assert app.BUS_NAME == service.BUS_NAME
    assert app.OBJECT_PATH == service.OBJECT_PATH
    assert app.ERROR_PREFIX == service.ERROR_PREFIX


def test_interface_name_matches_the_service() -> None:
    assert app.MEETINGS_INTERFACE == meetings_interface.INTERFACE_NAME


def test_every_member_the_app_calls_exists_on_the_service_interface() -> None:
    members = {name for name in dir(meetings_interface.MeetingsInterface) if name[0].isupper()}
    assert set(app.METHODS) <= members
    assert set(app.SIGNALS) <= members


def test_summaries_interface_name_matches_the_service() -> None:
    assert app.SUMMARIES_INTERFACE == summaries_interface.INTERFACE_NAME


def test_every_summaries_member_the_app_uses_exists_on_the_service_interface() -> None:
    members = {name for name in dir(summaries_interface.SummariesInterface) if name[0].isupper()}
    assert set(app.SUMMARY_METHODS) <= members
    assert set(app.SUMMARY_SIGNALS) <= members
