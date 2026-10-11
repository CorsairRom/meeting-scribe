"""The session routes speaker actions to the presenters; nothing plays on its own (AC-14)."""

from meeting_scribe_review.core.presenters.merge import MergeStatus
from meeting_scribe_review.core.presenters.rename import RenameStatus
from meeting_scribe_review.core.presenters.review import ReviewStatus
from meeting_scribe_review.core.result import Ok
from meeting_scribe_review.core.session import ReviewSession
from tests.unit.review_app.fakes import (
    FakeGateway,
    FakePlayer,
    FakeSummariesGateway,
    FakeViews,
    meeting,
    speaker,
)

SPEAKERS = [speaker("S1"), speaker("S2"), speaker("S3", merged_into="S1")]


def _open() -> tuple[ReviewSession, FakeGateway, FakePlayer, FakeViews]:
    gateway = FakeGateway()
    gateway.replies["list_meetings"] = Ok([meeting("m2", day=10), meeting("m1", day=9)])
    gateway.replies["get_speakers"] = Ok(SPEAKERS)
    player, views = FakePlayer(), FakeViews()
    session = ReviewSession(gateway, player, views, FakeSummariesGateway())
    session.start()
    return session, gateway, player, views


def test_nothing_plays_until_a_play_button_is_pressed() -> None:
    session, _, player, _ = _open()
    session.select("m1")
    assert player.played == []


def test_a_press_fetches_the_sample_and_plays_it() -> None:
    session, gateway, player, views = _open()
    session.play("S1")
    assert gateway.args_of("get_speaker_sample") == [("m2", "S1")]
    assert player.played == [b"RIFFfake"]
    assert views.playbacks[-1].playing_id == "S1"


def test_switching_meeting_stops_playback() -> None:
    session, _, player, _ = _open()
    session.play("S1")
    session.select("m1")
    assert player.events[-1][0] == "stop"


def test_closing_stops_playback() -> None:
    session, _, player, _ = _open()
    session.play("S1")
    session.close()
    assert player.events[-1][0] == "stop"


def test_a_sample_is_kept_until_the_meeting_is_processed_again() -> None:
    session, gateway, _, _ = _open()
    session.play("S1")
    session.stop_playback()
    session.play("S1")
    assert len(gateway.args_of("get_speaker_sample")) == 1
    session.stop_playback()
    gateway.listeners[0].on_processing_state_changed("m2", "QUEUED")
    session.play("S1")
    assert len(gateway.args_of("get_speaker_sample")) == 2


def test_a_rename_updates_the_review_and_plays_nothing() -> None:
    session, gateway, player, views = _open()
    renamed = [speaker("S1", name="Fake Alex"), speaker("S2")]
    gateway.replies["rename_speaker"] = Ok(renamed)
    session.rename("S1")
    assert views.renames[-1].status is RenameStatus.EDITING
    session.submit_rename("Fake Alex")
    assert gateway.args_of("rename_speaker") == [("m2", "S1", "Fake Alex")]
    assert views.renames[-1].status is RenameStatus.IDLE
    assert views.reviews[-1].speakers == tuple(renamed)
    assert len(gateway.args_of("get_transcript")) == 2
    assert player.played == []


def test_a_taken_name_can_be_resolved_by_merging_into_that_speaker() -> None:
    session, gateway, _, views = _open()
    session.rename("S1")
    session.submit_rename("Speaker 2")
    assert views.renames[-1].status is RenameStatus.CONFLICT
    session.merge_into_conflict()
    assert gateway.args_of("merge_speakers") == [("m2", "S1", "S2")]


def test_a_taken_name_can_be_edited_instead() -> None:
    session, _, _, views = _open()
    session.rename("S1")
    session.submit_rename("Speaker 2")
    session.edit_again()
    assert views.renames[-1].status is RenameStatus.EDITING


def test_cancelling_a_rename_closes_the_editor() -> None:
    session, _, _, views = _open()
    session.rename("S1")
    session.cancel_rename()
    assert views.renames[-1].status is RenameStatus.IDLE


def test_a_merge_asks_for_a_target_then_updates_the_review() -> None:
    session, gateway, _, views = _open()
    merged = [speaker("S1"), speaker("S2", merged_into="S1")]
    gateway.replies["merge_speakers"] = Ok(merged)
    session.merge("S2")
    assert views.merges[-1].status is MergeStatus.CHOOSING
    session.confirm_merge("S1")
    assert gateway.args_of("merge_speakers") == [("m2", "S2", "S1")]
    assert views.merges[-1].status is MergeStatus.IDLE
    assert views.reviews[-1].speakers == tuple(merged)


def test_cancelling_a_merge_closes_the_chooser() -> None:
    session, gateway, _, views = _open()
    session.merge("S2")
    session.cancel_merge()
    assert views.merges[-1].status is MergeStatus.IDLE
    assert gateway.args_of("merge_speakers") == []


def test_unmerge_calls_the_service_and_updates_the_review() -> None:
    session, gateway, _, views = _open()
    restored = [speaker("S1"), speaker("S2"), speaker("S3")]
    gateway.replies["unmerge_speaker"] = Ok(restored)
    session.unmerge("S3")
    assert gateway.args_of("unmerge_speaker") == [("m2", "S3")]
    assert views.reviews[-1].speakers == tuple(restored)
    assert views.reviews[-1].status is ReviewStatus.READY
