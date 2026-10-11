"""The session wires the meeting list to the review pane and fans service events out (AC-13)."""

from meeting_scribe_review.core.models import MeetingSummary
from meeting_scribe_review.core.presenters.meeting_list import MeetingListState, MeetingListStatus
from meeting_scribe_review.core.presenters.review import ReviewStatus
from meeting_scribe_review.core.result import Ok
from meeting_scribe_review.core.session import ReviewSession
from tests.unit.review_app.fakes import FakeGateway, FakePlayer, FakeViews, meeting, speaker


def _session(*meetings: MeetingSummary) -> tuple[ReviewSession, FakeGateway, FakeViews]:
    gateway = FakeGateway()
    gateway.replies["list_meetings"] = Ok(list(meetings))
    gateway.replies["get_speakers"] = Ok([speaker("S1"), speaker("S2")])
    views = FakeViews()
    return ReviewSession(gateway, FakePlayer(), views), gateway, views


def test_start_opens_the_newest_done_meeting() -> None:
    session, gateway, views = _session(meeting("m3", "QUEUED", day=11), meeting("m2", day=10))
    session.start()
    assert views.meetings[-1].selected_id == "m2"
    assert gateway.args_of("get_speakers") == [("m2",)]
    assert views.reviews[-1].status is ReviewStatus.READY


def test_start_prefers_the_requested_meeting() -> None:
    session, gateway, _ = _session(meeting("m2", day=10), meeting("m1", day=9))
    session.start("m1")
    assert gateway.args_of("get_speakers") == [("m1",)]


def test_selecting_another_meeting_opens_its_review() -> None:
    session, gateway, _ = _session(meeting("m2", day=10), meeting("m1", day=9))
    session.start()
    session.select("m1")
    assert gateway.args_of("get_speakers") == [("m2",), ("m1",)]


def test_a_meeting_that_is_not_done_opens_read_only_without_asking_for_speakers() -> None:
    session, gateway, views = _session(meeting("m2", "DIARIZING", day=10), meeting("m1", day=9))
    session.start()
    session.select("m2")
    assert views.reviews[-1].status is ReviewStatus.READ_ONLY
    assert gateway.args_of("get_speakers") == [("m1",)]


def test_a_refresh_that_keeps_the_selection_does_not_reopen_the_review() -> None:
    session, gateway, _ = _session(meeting("m2", day=10))
    session.start()
    session.start()
    assert gateway.args_of("get_speakers") == [("m2",)]


def test_no_reviewable_meeting_shows_the_empty_state_and_leaves_the_review_idle() -> None:
    session, _, views = _session(meeting("m1", "QUEUED"))
    session.start()
    assert views.meetings[-1].status is MeetingListStatus.EMPTY
    assert session.review.status is ReviewStatus.IDLE


def test_service_events_reach_both_presenters() -> None:
    session, gateway, _ = _session(meeting("m2", day=10))
    session.start()
    assert len(gateway.listeners) == 1
    gateway.listeners[0].on_speakers_changed("m2")
    assert len(gateway.args_of("list_meetings")) == 2
    assert len(gateway.args_of("get_speakers")) == 2


def test_a_service_that_goes_away_shows_the_error_in_both_panes() -> None:
    session, gateway, views = _session(meeting("m2", day=10))
    session.start()
    gateway.listeners[0].on_service_available(False)
    assert views.meetings[-1].status is MeetingListStatus.ERROR
    assert views.reviews[-1].status is ReviewStatus.ERROR


def test_retry_reloads_the_list() -> None:
    session, gateway, _ = _session(meeting("m2", day=10))
    session.start()
    session.retry()
    assert len(gateway.args_of("list_meetings")) == 2


def test_close_empties_the_review() -> None:
    session, _, views = _session(meeting("m2", day=10))
    session.start()
    session.close()
    assert views.reviews[-1].status is ReviewStatus.IDLE


def test_a_selection_that_is_not_in_the_rows_closes_the_review() -> None:
    session, _, _ = _session(meeting("m2", day=10))
    session.start()
    gone = MeetingListState(MeetingListStatus.READY, (), "m9")
    session._meetings_changed(gone)
    assert session.review.status is ReviewStatus.IDLE
