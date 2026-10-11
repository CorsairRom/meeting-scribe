"""Merge and unmerge (AC-4, AC-5, AC-6, US-4)."""

from meeting_scribe_review.core.models import Speaker
from meeting_scribe_review.core.presenters.merge import (
    MergePresenter,
    MergeState,
    MergeStatus,
    MergeTarget,
)
from meeting_scribe_review.core.result import Ok
from tests.unit.review_app.fakes import FakeGateway, speaker

SPEAKERS = [
    speaker("ME", is_me=True),
    speaker("S1", name="Fake Alex"),
    speaker("S2"),
    speaker("S3", merged_into="S1"),
]


def _presenter(
    gateway: FakeGateway,
) -> tuple[MergePresenter, list[MergeState], list[tuple[str, list[Speaker]]]]:
    seen: list[MergeState] = []
    applied: list[tuple[str, list[Speaker]]] = []
    presenter = MergePresenter(gateway, seen.append, lambda mid, rows: applied.append((mid, rows)))
    return presenter, seen, applied


def test_starting_a_merge_offers_the_other_active_speakers_as_targets() -> None:
    presenter, _, _ = _presenter(FakeGateway())
    presenter.start("m1", "S2", SPEAKERS)
    state = presenter.state
    assert state.status is MergeStatus.CHOOSING and state.source_id == "S2"
    assert state.targets == (MergeTarget("ME", "Me"), MergeTarget("S1", "Fake Alex"))


def test_me_is_never_offered_as_a_source() -> None:
    presenter, _, _ = _presenter(FakeGateway())
    presenter.start("m1", "ME", SPEAKERS)
    assert presenter.state.status is MergeStatus.IDLE


def test_a_merged_away_speaker_cannot_start_a_merge() -> None:
    presenter, _, _ = _presenter(FakeGateway())
    presenter.start("m1", "S3", SPEAKERS)
    assert presenter.state.status is MergeStatus.IDLE


def test_a_speaker_with_no_other_active_speaker_cannot_start_a_merge() -> None:
    presenter, _, _ = _presenter(FakeGateway())
    presenter.start("m1", "S1", [speaker("S1")])
    assert presenter.state.status is MergeStatus.IDLE


def test_confirming_merges_source_into_target_and_reports_the_rows() -> None:
    gateway = FakeGateway()
    rows = [speaker("S1")]
    gateway.replies["merge_speakers"] = Ok(rows)
    presenter, _, applied = _presenter(gateway)
    presenter.start("m1", "S2", SPEAKERS)
    presenter.confirm("S1")
    assert gateway.names_of("merge_speakers") == [("m1", "S2", "S1")]
    assert applied == [("m1", rows)]
    assert presenter.state.status is MergeStatus.IDLE


def test_a_target_that_was_not_offered_is_ignored() -> None:
    gateway = FakeGateway()
    presenter, _, _ = _presenter(gateway)
    presenter.start("m1", "S2", SPEAKERS)
    presenter.confirm("S3")
    assert gateway.calls == []


def test_merging_directly_is_used_by_the_duplicate_name_flow() -> None:
    gateway = FakeGateway()
    presenter, _, applied = _presenter(gateway)
    presenter.merge("m1", "S2", "S1")
    assert gateway.names_of("merge_speakers") == [("m1", "S2", "S1")]
    assert len(applied) == 1


def test_a_second_press_while_busy_is_ignored() -> None:
    gateway = FakeGateway(deferred=True)
    presenter, _, _ = _presenter(gateway)
    presenter.start("m1", "S2", SPEAKERS)
    presenter.confirm("S1")
    assert presenter.state.status is MergeStatus.BUSY
    presenter.confirm("ME")
    assert len(gateway.names_of("merge_speakers")) == 1


def test_a_service_refusal_shows_its_message_and_applies_nothing() -> None:
    gateway = FakeGateway()
    gateway.fail("merge_speakers", "SPEAKER_MERGE_INVALID")
    presenter, _, applied = _presenter(gateway)
    presenter.start("m1", "S2", SPEAKERS)
    presenter.confirm("S1")
    assert presenter.state.status is MergeStatus.ERROR
    assert presenter.state.message == "These speakers cannot be merged."
    assert applied == []


def test_cancel_closes_the_dialog() -> None:
    presenter, _, _ = _presenter(FakeGateway())
    presenter.start("m1", "S2", SPEAKERS)
    presenter.cancel()
    assert presenter.state == MergeState()


def test_unmerge_restores_the_speaker_and_reports_the_rows() -> None:
    gateway = FakeGateway()
    presenter, _, applied = _presenter(gateway)
    presenter.unmerge("m1", "S3")
    assert gateway.names_of("unmerge_speaker") == [("m1", "S3")]
    assert len(applied) == 1


def test_unmerge_that_clashes_on_the_name_explains_what_to_do() -> None:
    gateway = FakeGateway()
    gateway.fail("unmerge_speaker", "SPEAKER_NAME_TAKEN")
    presenter, _, applied = _presenter(gateway)
    presenter.unmerge("m1", "S3")
    assert presenter.state.status is MergeStatus.ERROR
    assert "rename" in presenter.state.message.lower()
    assert applied == []
