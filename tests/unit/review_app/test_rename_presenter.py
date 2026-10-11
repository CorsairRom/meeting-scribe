"""Rename, including the duplicate-name flow: merge into that speaker or edit the name (OQ-3)."""

from meeting_scribe_review.core.models import Speaker
from meeting_scribe_review.core.presenters.rename import (
    NameConflict,
    RenamePresenter,
    RenameState,
    RenameStatus,
)
from meeting_scribe_review.core.result import Ok
from tests.unit.review_app.fakes import FakeGateway, speaker

SPEAKERS = [
    speaker("ME", is_me=True),
    speaker("S1", name="Fake Alex"),
    speaker("S2"),
    speaker("S3", merged_into="S1", name="Fake Old"),
]


class _Rig:
    def __init__(self, gateway: FakeGateway) -> None:
        self.gateway = gateway
        self.seen: list[RenameState] = []
        self.applied: list[tuple[str, list[Speaker]]] = []
        self.merges: list[tuple[str, str, str]] = []
        self.presenter = RenamePresenter(
            gateway,
            self.seen.append,
            lambda mid, rows: self.applied.append((mid, rows)),
            lambda mid, source, target: self.merges.append((mid, source, target)),
        )

    @property
    def state(self) -> RenameState:
        return self.presenter.state


def test_starting_shows_the_current_name_as_the_draft() -> None:
    rig = _Rig(FakeGateway())
    rig.presenter.start("m1", "S1", SPEAKERS)
    assert rig.state.status is RenameStatus.EDITING
    assert rig.state.speaker_id == "S1" and rig.state.draft == "Fake Alex"


def test_a_merged_speaker_cannot_be_renamed() -> None:
    rig = _Rig(FakeGateway())
    rig.presenter.start("m1", "S3", SPEAKERS)
    assert rig.state.status is RenameStatus.IDLE


def test_submit_sends_the_trimmed_name_and_reports_the_rows() -> None:
    gateway = FakeGateway()
    rows = [speaker("S2", name="Fake Bea")]
    gateway.replies["rename_speaker"] = Ok(rows)
    rig = _Rig(gateway)
    rig.presenter.start("m1", "S2", SPEAKERS)
    rig.presenter.submit("  Fake Bea  ")
    assert gateway.names_of("rename_speaker") == [("m1", "S2", "Fake Bea")]
    assert rig.applied == [("m1", rows)]
    assert rig.state.status is RenameStatus.IDLE


def test_an_invalid_name_shows_feedback_without_calling_the_service() -> None:
    rig = _Rig(FakeGateway())
    rig.presenter.start("m1", "S2", SPEAKERS)
    rig.presenter.submit("x" * 65)
    assert rig.state.status is RenameStatus.EDITING
    assert rig.state.problem == "A name has at most 64 characters."
    assert rig.gateway.calls == []


def test_the_same_name_is_a_no_op_without_a_call() -> None:
    rig = _Rig(FakeGateway())
    rig.presenter.start("m1", "S1", SPEAKERS)
    rig.presenter.submit(" Fake Alex ")
    assert rig.gateway.calls == [] and rig.state.status is RenameStatus.IDLE
    assert rig.applied == []


def test_an_empty_name_clears_the_name() -> None:
    rig = _Rig(FakeGateway())
    rig.presenter.start("m1", "S1", SPEAKERS)
    rig.presenter.submit("   ")
    assert rig.gateway.names_of("rename_speaker") == [("m1", "S1", "")]


def test_a_taken_name_offers_to_merge_or_to_edit() -> None:
    rig = _Rig(FakeGateway())
    rig.presenter.start("m1", "S2", SPEAKERS)
    rig.presenter.submit("fake alex")
    assert rig.state.status is RenameStatus.CONFLICT
    assert rig.state.conflict == NameConflict("S1", "Fake Alex", can_merge=True)
    assert rig.gateway.calls == []


def test_merging_from_the_conflict_merges_into_that_speaker_and_closes() -> None:
    rig = _Rig(FakeGateway())
    rig.presenter.start("m1", "S2", SPEAKERS)
    rig.presenter.submit("Fake Alex")
    rig.presenter.merge_into_conflict()
    assert rig.merges == [("m1", "S2", "S1")]
    assert rig.state.status is RenameStatus.IDLE


def test_editing_again_after_a_conflict_keeps_the_typed_name() -> None:
    rig = _Rig(FakeGateway())
    rig.presenter.start("m1", "S2", SPEAKERS)
    rig.presenter.submit("Fake Alex")
    rig.presenter.edit_again()
    assert rig.state.status is RenameStatus.EDITING and rig.state.draft == "Fake Alex"
    assert rig.state.conflict is None


def test_me_cannot_be_offered_a_merge() -> None:
    rig = _Rig(FakeGateway())
    rig.presenter.start("m1", "ME", SPEAKERS)
    rig.presenter.submit("Fake Alex")
    assert rig.state.conflict == NameConflict("S1", "Fake Alex", can_merge=False)
    rig.presenter.merge_into_conflict()
    assert rig.merges == []


def test_a_clash_only_the_service_knows_about_shows_the_message() -> None:
    gateway = FakeGateway()
    gateway.fail("rename_speaker", "SPEAKER_NAME_TAKEN")
    rig = _Rig(gateway)
    # The client rows are stale: another client named S1 meanwhile, only the service knows.
    rig.presenter.start("m1", "S2", [speaker("S1"), speaker("S2")])
    rig.presenter.submit("Fake Alex")
    assert rig.state.status is RenameStatus.EDITING
    assert rig.state.problem == "That name is already used by another speaker."
    assert rig.state.conflict is None


def test_another_service_error_shows_its_message_and_keeps_editing() -> None:
    gateway = FakeGateway()
    gateway.fail("rename_speaker", "MEETING_NOT_PROCESSED")
    rig = _Rig(gateway)
    rig.presenter.start("m1", "S2", SPEAKERS)
    rig.presenter.submit("Fake Bea")
    assert rig.state.status is RenameStatus.EDITING
    assert rig.state.problem == "This meeting is not processed yet."
    assert rig.applied == []


def test_a_second_submit_while_busy_is_ignored() -> None:
    gateway = FakeGateway(deferred=True)
    rig = _Rig(gateway)
    rig.presenter.start("m1", "S2", SPEAKERS)
    rig.presenter.submit("Fake Bea")
    assert rig.state.status is RenameStatus.BUSY
    rig.presenter.submit("Fake Cy")
    assert len(gateway.names_of("rename_speaker")) == 1


def test_cancel_closes_the_dialog() -> None:
    rig = _Rig(FakeGateway())
    rig.presenter.start("m1", "S2", SPEAKERS)
    rig.presenter.cancel()
    assert rig.state == RenameState()
