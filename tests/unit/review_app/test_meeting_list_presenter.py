"""The meeting list opens the newest DONE meeting and lists the others read-only (AC-13, US-6)."""

from meeting_scribe_review.core.errors import SERVICE_NOT_RUNNING
from meeting_scribe_review.core.presenters.meeting_list import (
    MeetingListPresenter,
    MeetingListState,
    MeetingListStatus,
)
from meeting_scribe_review.core.result import Ok
from tests.unit.review_app.fakes import FakeGateway, meeting


def _presenter(gateway: FakeGateway) -> tuple[MeetingListPresenter, list[MeetingListState]]:
    seen: list[MeetingListState] = []
    return MeetingListPresenter(gateway, seen.append), seen


def test_it_selects_the_newest_done_meeting() -> None:
    gateway = FakeGateway()
    gateway.replies["list_meetings"] = Ok(
        [meeting("m3", "TRANSCRIBING", day=11), meeting("m2", day=10), meeting("m1", day=9)]
    )
    presenter, _ = _presenter(gateway)
    presenter.load()
    state = presenter.state
    assert state.status is MeetingListStatus.READY
    assert state.selected_id == "m2"
    assert [row.id for row in state.rows] == ["m3", "m2", "m1"]


def test_rows_say_whether_a_meeting_can_be_reviewed_and_show_its_stage() -> None:
    gateway = FakeGateway()
    gateway.replies["list_meetings"] = Ok(
        [meeting("m3", "DIARIZING", day=11), meeting("m2", day=10, speakers=3)]
    )
    presenter, _ = _presenter(gateway)
    presenter.load()
    busy, done = presenter.state.rows
    assert (busy.reviewable, busy.state_label) == (False, "Identifying speakers")
    assert (done.reviewable, done.state_label, done.speaker_count) == (True, "Ready", 3)
    assert done.duration_text == "12 min"


def test_with_no_done_meeting_it_shows_the_empty_state_and_the_newest_stage() -> None:
    gateway = FakeGateway()
    gateway.replies["list_meetings"] = Ok(
        [meeting("m2", "TRANSCRIBING", day=11), meeting("m1", "FAILED")]
    )
    presenter, _ = _presenter(gateway)
    presenter.load()
    state = presenter.state
    assert state.status is MeetingListStatus.EMPTY
    assert state.selected_id == ""
    assert state.message == "No reviewed meetings yet"
    assert state.detail == "Latest meeting: Transcribing"


def test_with_no_meeting_at_all_the_empty_state_has_no_detail() -> None:
    presenter, _ = _presenter(FakeGateway())
    presenter.load()
    assert presenter.state.status is MeetingListStatus.EMPTY
    assert (presenter.state.message, presenter.state.detail) == ("No reviewed meetings yet", "")


def test_a_requested_meeting_wins_even_when_it_is_not_done() -> None:
    gateway = FakeGateway()
    gateway.replies["list_meetings"] = Ok([meeting("m2", day=10), meeting("m1", "QUEUED")])
    presenter, _ = _presenter(gateway)
    presenter.load(preferred_id="m1")
    assert presenter.state.selected_id == "m1"


def test_an_unknown_requested_meeting_falls_back_to_the_newest_done() -> None:
    gateway = FakeGateway()
    gateway.replies["list_meetings"] = Ok([meeting("m2", day=10)])
    presenter, _ = _presenter(gateway)
    presenter.load(preferred_id="gone")
    assert presenter.state.selected_id == "m2"


def test_the_user_can_select_another_meeting() -> None:
    gateway = FakeGateway()
    gateway.replies["list_meetings"] = Ok([meeting("m2", day=10), meeting("m1")])
    presenter, seen = _presenter(gateway)
    presenter.load()
    presenter.select("m1")
    assert presenter.state.selected_id == "m1"
    assert seen[-1].selected_id == "m1"


def test_selecting_an_unknown_meeting_is_ignored() -> None:
    gateway = FakeGateway()
    gateway.replies["list_meetings"] = Ok([meeting("m1")])
    presenter, _ = _presenter(gateway)
    presenter.load()
    presenter.select("nope")
    assert presenter.state.selected_id == "m1"


def test_a_failure_shows_its_message_and_next_step() -> None:
    gateway = FakeGateway()
    gateway.fail("list_meetings", SERVICE_NOT_RUNNING)
    presenter, _ = _presenter(gateway)
    presenter.load()
    state = presenter.state
    assert state.status is MeetingListStatus.ERROR
    assert state.message == "The Meeting Scribe service is not running"
    assert state.detail == "systemctl --user start meeting-scribe.service"


def test_service_events_refresh_the_list_and_keep_the_selection() -> None:
    gateway = FakeGateway()
    gateway.replies["list_meetings"] = Ok([meeting("m2", day=10), meeting("m1")])
    presenter, _ = _presenter(gateway)
    presenter.load()
    presenter.select("m1")
    gateway.replies["list_meetings"] = Ok(
        [meeting("m3", day=11), meeting("m2", day=10), meeting("m1")]
    )
    presenter.on_processing_finished("m3", "DONE", 2)
    assert presenter.state.selected_id == "m1"
    assert len(presenter.state.rows) == 3
    for event in (
        lambda: presenter.on_processing_state_changed("m3", "DONE"),
        lambda: presenter.on_speakers_changed("m3"),
    ):
        event()
    assert len(gateway.names_of("list_meetings")) == 4


def test_a_meeting_that_finishes_is_selected_when_nothing_was() -> None:
    gateway = FakeGateway()
    gateway.replies["list_meetings"] = Ok([meeting("m1", "DIARIZING")])
    presenter, _ = _presenter(gateway)
    presenter.load()
    assert presenter.state.status is MeetingListStatus.EMPTY
    gateway.replies["list_meetings"] = Ok([meeting("m1", "DONE")])
    presenter.on_processing_finished("m1", "DONE", 2)
    assert presenter.state.selected_id == "m1"


def test_losing_the_service_shows_the_not_running_state_and_its_return_reloads() -> None:
    gateway = FakeGateway()
    gateway.replies["list_meetings"] = Ok([meeting("m1")])
    presenter, _ = _presenter(gateway)
    presenter.load()
    presenter.on_service_available(False)
    assert presenter.state.status is MeetingListStatus.ERROR
    assert presenter.state.message == "The Meeting Scribe service is not running"
    presenter.on_service_available(True)
    assert presenter.state.status is MeetingListStatus.READY
