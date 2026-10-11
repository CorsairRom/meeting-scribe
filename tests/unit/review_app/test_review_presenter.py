"""The review pane: speakers and transcript of one meeting (AC-1, AC-8, AC-9, edge cases)."""

from meeting_scribe_review.core.models import Utterance
from meeting_scribe_review.core.presenters.review import (
    ReviewPresenter,
    ReviewState,
    ReviewStatus,
)
from meeting_scribe_review.core.result import Ok
from tests.unit.review_app.fakes import FakeGateway, speaker

SPEAKERS = [
    speaker("ME", is_me=True, talk_seconds=300.0, utterance_count=4),
    speaker("S1", name="Fake Alex", talk_seconds=125.0, utterance_count=1),
    speaker("S2", has_sample=False, talk_seconds=40.0),
    speaker("S3", merged_into="S1", name="Fake Old"),
]
LINES = [Utterance("S1", 0.0, 2.0, "Fake one."), Utterance("S1", 3.0, 4.0, "Fake two.")]


def _open(
    gateway: FakeGateway, state: str = "DONE", meeting_id: str = "m1"
) -> tuple[ReviewPresenter, list[ReviewState]]:
    seen: list[ReviewState] = []
    presenter = ReviewPresenter(gateway, seen.append)
    presenter.open(meeting_id, state)
    return presenter, seen


def _gateway(speakers=SPEAKERS, lines=LINES) -> FakeGateway:
    gateway = FakeGateway()
    gateway.replies["get_speakers"] = Ok(list(speakers))
    gateway.replies["get_transcript"] = Ok(list(lines))
    return gateway


def test_a_done_meeting_loads_speakers_and_transcript() -> None:
    presenter, _ = _open(_gateway())
    state = presenter.state
    assert state.status is ReviewStatus.READY and state.editable
    assert [row.id for row in state.active] == ["ME", "S1", "S2"]
    assert [row.id for row in state.merged] == ["S3"]
    assert [(line.speaker_name, line.timestamp) for line in state.transcript] == [
        ("Fake Alex", "00:00"),
        ("Fake Alex", "00:03"),
    ]


def test_it_goes_through_loading_first() -> None:
    _, seen = _open(_gateway())
    assert seen[0].status is ReviewStatus.LOADING
    assert seen[-1].status is ReviewStatus.READY


def test_a_speaker_row_shows_display_label_and_totals() -> None:
    row = _open(_gateway())[0].state.active[1]
    assert (row.display, row.detail) == ("Fake Alex", "Speaker 1")
    assert row.summary == "2 min · 1 turn"
    unnamed = _open(_gateway())[0].state.active[2]
    assert unnamed.detail == ""


def test_the_row_without_a_sample_has_no_play_and_says_no_sample() -> None:
    rows = {row.id: row for row in _open(_gateway())[0].state.active}
    assert rows["S2"].can_play is False and rows["S2"].sample_text == "No sample"
    assert rows["S1"].can_play is True and rows["S1"].sample_text == ""


def test_me_can_be_renamed_but_never_merged_as_a_source() -> None:
    rows = {row.id: row for row in _open(_gateway())[0].state.active}
    assert rows["ME"].can_rename and not rows["ME"].can_merge
    assert rows["S1"].can_rename and rows["S1"].can_merge


def test_a_merged_speaker_keeps_its_sample_and_offers_unmerge_only() -> None:
    merged = _open(_gateway())[0].state.merged[0]
    assert merged.merged_into_display == "Fake Alex"
    assert merged.can_play and merged.can_unmerge
    assert not merged.can_rename and not merged.can_merge


def test_a_solo_recording_cannot_be_merged() -> None:
    state = _open(_gateway([speaker("ME", is_me=True)]))[0].state
    assert state.active[0].can_rename and not state.active[0].can_merge


def test_a_meeting_without_speech_shows_the_empty_text_and_no_edit_controls() -> None:
    state = _open(_gateway([], []))[0].state
    assert state.status is ReviewStatus.READY
    assert state.empty_text == "No speech detected"
    assert not state.editable and state.active == () and state.transcript == ()


def test_a_meeting_still_processing_is_read_only_with_a_banner() -> None:
    gateway = _gateway()
    presenter, _ = _open(gateway, "TRANSCRIBING")
    state = presenter.state
    assert state.status is ReviewStatus.READ_ONLY and not state.editable
    assert "Transcribing" in state.banner
    assert gateway.calls == []


def test_a_failed_meeting_points_to_the_panel_menu() -> None:
    state = _open(_gateway(), "FAILED")[0].state
    assert state.status is ReviewStatus.READ_ONLY
    assert "panel menu" in state.banner


def test_finishing_while_open_loads_the_review() -> None:
    gateway = _gateway()
    presenter, _ = _open(gateway, "DIARIZING")
    presenter.on_processing_finished("m1", "DONE", 3)
    assert presenter.state.status is ReviewStatus.READY
    assert len(presenter.state.active) == 3


def test_a_reprocess_while_open_makes_the_pane_read_only_then_reloads() -> None:
    presenter, _ = _open(_gateway())
    presenter.on_processing_state_changed("m1", "QUEUED")
    assert presenter.state.status is ReviewStatus.READ_ONLY
    assert presenter.state.active == ()
    presenter.on_processing_state_changed("m1", "DONE")
    assert presenter.state.status is ReviewStatus.READY


def test_events_of_other_meetings_are_ignored() -> None:
    gateway = _gateway()
    presenter, _ = _open(gateway)
    before = len(gateway.calls)
    presenter.on_speakers_changed("other")
    presenter.on_processing_state_changed("other", "QUEUED")
    assert len(gateway.calls) == before and presenter.state.status is ReviewStatus.READY


def test_speakers_changed_reloads_without_flashing_the_loading_state() -> None:
    gateway = _gateway()
    presenter, seen = _open(gateway)
    count = len(seen)
    gateway.replies["get_speakers"] = Ok([speaker("S1", name="Fake Zed")])
    presenter.on_speakers_changed("m1")
    assert all(s.status is ReviewStatus.READY for s in seen[count:])
    assert presenter.state.active[0].display == "Fake Zed"


def test_applying_an_edit_result_updates_the_rows_and_refreshes_the_transcript() -> None:
    gateway = _gateway()
    presenter, _ = _open(gateway)
    gateway.replies["get_transcript"] = Ok([Utterance("S2", 0.0, 1.0, "Fake three.")])
    presenter.apply_speakers("m1", [speaker("S2", name="Fake Bea")])
    assert presenter.state.active[0].display == "Fake Bea"
    assert presenter.state.transcript[0].speaker_name == "Fake Bea"


def test_a_late_reply_for_a_previous_meeting_is_dropped() -> None:
    gateway = _gateway()
    gateway.deferred = True
    presenter = ReviewPresenter(gateway, lambda _state: None)
    presenter.open("m1", "DONE")
    presenter.open("m2", "DONE")
    gateway.release(0)  # the speakers of m1 arrive after m2 was opened
    assert gateway.names_of("get_transcript") == []
    gateway.release(0)  # the speakers of m2
    gateway.release(0)  # the transcript of m2
    assert presenter.state.meeting_id == "m2" and presenter.state.status is ReviewStatus.READY


def test_a_failure_shows_the_message_for_its_code() -> None:
    gateway = _gateway()
    gateway.fail("get_transcript", "MEETING_CORRUPT")
    state = _open(gateway)[0].state
    assert state.status is ReviewStatus.ERROR
    assert state.message == "This meeting's transcript is damaged. Reprocess it."
    assert not state.editable


def test_not_processed_from_the_service_becomes_the_read_only_banner() -> None:
    gateway = _gateway()
    gateway.fail("get_speakers", "MEETING_NOT_PROCESSED")
    state = _open(gateway)[0].state
    assert state.status is ReviewStatus.READ_ONLY
    assert state.banner == "This meeting is not processed yet."


def test_losing_and_regaining_the_service_while_open() -> None:
    presenter, _ = _open(_gateway())
    presenter.on_service_available(False)
    assert presenter.state.status is ReviewStatus.ERROR
    assert presenter.state.hint == "systemctl --user start meeting-scribe.service"
    presenter.on_service_available(True)
    assert presenter.state.status is ReviewStatus.READY


def test_retry_reloads_the_open_meeting() -> None:
    gateway = _gateway()
    gateway.fail("get_speakers", "MEETING_STORAGE_FAILED")
    presenter, _ = _open(gateway)
    gateway.replies["get_speakers"] = Ok(list(SPEAKERS))
    presenter.reload()
    assert presenter.state.status is ReviewStatus.READY


def test_close_returns_to_idle_and_ignores_later_replies() -> None:
    presenter, _ = _open(_gateway())
    presenter.close()
    assert presenter.state.status is ReviewStatus.IDLE and presenter.state.meeting_id == ""
    presenter.on_speakers_changed("m1")
    assert presenter.state.status is ReviewStatus.IDLE


def test_a_late_reply_cannot_ready_a_read_only_meeting_opened_after_it() -> None:
    gateway = _gateway()
    gateway.deferred = True
    presenter = ReviewPresenter(gateway, lambda _state: None)
    presenter.open("m1", "DONE")
    presenter.open("m2", "TRANSCRIBING")
    gateway.release(0)  # the speakers of m1 arrive after m2 was opened
    assert gateway.names_of("get_transcript") == []
    assert presenter.state.meeting_id == "m2"
    assert presenter.state.status is ReviewStatus.READ_ONLY and not presenter.state.editable
