"""The session opens the summary with the review, fans its signals out and confirms the cloud."""

from meeting_scribe_review.core.presenters.summary import SummaryPhase
from meeting_scribe_review.core.result import Ok
from meeting_scribe_review.core.session import ReviewSession
from tests.unit.review_app.fakes import (
    FakeGateway,
    FakePlayer,
    FakeSummariesGateway,
    FakeViews,
    meeting,
    provider,
    speaker,
    summary_info,
)


def _open(
    *, cloud: bool = False
) -> tuple[ReviewSession, FakeSummariesGateway, FakeViews, FakeGateway]:
    gateway = FakeGateway()
    gateway.replies["list_meetings"] = Ok([meeting("m2", day=10), meeting("m1", "QUEUED", day=9)])
    gateway.replies["get_speakers"] = Ok([speaker("S1")])
    summaries = FakeSummariesGateway()
    if cloud:
        summaries.replies["providers"] = Ok([provider("cloud", cloud=True)])
    views = FakeViews()
    session = ReviewSession(gateway, FakePlayer(), views, summaries)
    session.start()
    return session, summaries, views, gateway


def test_opening_a_done_meeting_loads_its_summary_without_generating() -> None:
    _session, summaries, views, _ = _open()
    assert summaries.args_of("get_summary_status") == [("m2",)]
    assert summaries.args_of("summarize") == []
    assert views.summaries[-1].phase is SummaryPhase.NONE


def test_a_meeting_that_is_not_done_has_no_summary_view() -> None:
    session, summaries, views, _ = _open()
    session.select("m1")
    assert views.summaries[-1].phase is SummaryPhase.IDLE
    assert summaries.args_of("get_summary_status") == [("m2",)]


def test_the_session_listens_to_summary_signals() -> None:
    session, summaries, views, _ = _open()
    assert summaries.listeners == [session]
    session.on_summary_progress("m2", 1, 3)
    assert views.summaries[-1].phase is SummaryPhase.RUNNING
    summaries.replies["get_summary_status"] = Ok(summary_info("DONE", generated=True))
    session.on_summary_finished("m2", "DONE", "")
    assert views.summaries[-1].phase is SummaryPhase.READY


def test_speakers_changed_refreshes_the_summary_status() -> None:
    session, summaries, _views, _ = _open()
    session.on_speakers_changed("m2")
    assert len(summaries.args_of("get_summary_status")) == 2


def test_a_local_generate_sends_at_once_and_cancel_is_forwarded() -> None:
    session, summaries, views, _ = _open()
    session.generate_summary()
    assert summaries.args_of("summarize") == [("m2", "local", False)]
    assert views.confirmations == []
    session.cancel_summary()
    assert summaries.args_of("cancel_summary") == [("m2",)]


def test_a_cloud_generate_sends_only_after_the_confirmation() -> None:
    session, summaries, views, _ = _open(cloud=True)
    session.generate_summary()
    assert views.confirmations[-1].open
    assert summaries.args_of("summarize") == []
    session.cancel_cloud()
    assert summaries.args_of("summarize") == []
    session.generate_summary()
    session.confirm_cloud()
    assert summaries.args_of("summarize") == [("m2", "cloud", True)]
    assert not views.confirmations[-1].open


def test_closing_the_window_forgets_the_summary_and_any_open_confirmation() -> None:
    session, summaries, views, _ = _open(cloud=True)
    session.generate_summary()
    session.close()
    assert views.summaries[-1].text == ""
    assert not views.confirmations[-1].open
    session.confirm_cloud()
    assert summaries.args_of("summarize") == []
