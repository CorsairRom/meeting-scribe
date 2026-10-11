"""The presenters wired as the views will wire them: edits refresh the review and never play."""

from meeting_scribe_review.core.models import Speaker, Utterance
from meeting_scribe_review.core.presenters.merge import MergePresenter
from meeting_scribe_review.core.presenters.playback import PlaybackPresenter
from meeting_scribe_review.core.presenters.rename import RenamePresenter, RenameStatus
from meeting_scribe_review.core.presenters.review import ReviewPresenter
from meeting_scribe_review.core.result import Ok
from tests.unit.review_app.fakes import FakeGateway, FakePlayer, speaker


def _wire() -> tuple[FakeGateway, FakePlayer, ReviewPresenter, RenamePresenter, MergePresenter]:
    gateway, player = FakeGateway(), FakePlayer()
    gateway.replies["get_speakers"] = Ok([speaker("S1", name="Fake Alex"), speaker("S2")])
    gateway.replies["get_transcript"] = Ok([Utterance("S1", 0.0, 1.0, "Fake.")])
    review = ReviewPresenter(gateway, lambda _state: None)
    merge = MergePresenter(gateway, lambda _state: None, review.apply_speakers)
    rename = RenamePresenter(gateway, lambda _state: None, review.apply_speakers, merge.merge)
    PlaybackPresenter(gateway, player, lambda _state: None)
    review.open("m1", "DONE")
    return gateway, player, review, rename, merge


def test_a_rename_refreshes_the_rows_and_plays_nothing() -> None:
    gateway, player, review, rename, _ = _wire()
    after: list[Speaker] = [speaker("S1", name="Fake Alex"), speaker("S2", name="Fake Bea")]
    gateway.replies["rename_speaker"] = Ok(after)
    rename.start("m1", "S2", review.state.speakers)
    rename.submit("Fake Bea")
    assert [row.display for row in review.state.active] == ["Fake Alex", "Fake Bea"]
    assert player.events == []


def test_a_duplicate_name_can_be_resolved_by_merging_into_that_speaker() -> None:
    gateway, player, review, rename, _ = _wire()
    gateway.replies["merge_speakers"] = Ok(
        [speaker("S1", name="Fake Alex"), speaker("S2", merged_into="S1")]
    )
    rename.start("m1", "S2", review.state.speakers)
    rename.submit("fake alex")
    assert rename.state.status is RenameStatus.CONFLICT
    rename.merge_into_conflict()
    assert gateway.args_of("merge_speakers") == [("m1", "S2", "S1")]
    assert [row.id for row in review.state.merged] == ["S2"]
    assert gateway.args_of("rename_speaker") == []
    assert player.events == []


def test_an_unmerge_brings_the_speaker_back_into_the_active_rows() -> None:
    gateway, _, review, _, merge = _wire()
    gateway.replies["unmerge_speaker"] = Ok([speaker("S1"), speaker("S2")])
    merge.unmerge("m1", "S2")
    assert [row.id for row in review.state.active] == ["S1", "S2"]
