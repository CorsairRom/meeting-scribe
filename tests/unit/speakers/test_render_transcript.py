from pathlib import Path

from returns.result import Failure, Success

from meeting_scribe.speakers.application.render_transcript import RenderTranscript
from meeting_scribe.speakers.domain.errors import (
    TranscriptCorruptError,
    TranscriptStorageError,
)
from meeting_scribe.speakers.domain.review import ReviewState, SpeakerRecord
from meeting_scribe.speakers.domain.turns import Utterance
from tests.unit.speakers.fakes import InMemoryUtterancesStore

MEETING = Path("/meetings/m1")
UTTERANCES = (
    Utterance(speaker="S1", start=1.0, end=2.0, text="first"),
    Utterance(speaker="S3", start=65.0, end=70.0, text="second"),
    Utterance(speaker="ME", start=3725.0, end=3726.0, text="third"),
)


def _state(merged_into: dict[str, str] | None = None) -> ReviewState:
    return ReviewState(
        speakers=(
            SpeakerRecord(id="ME", label="Me", is_me=True),
            SpeakerRecord(id="S1", label="Speaker 1", name="Alex_"),
            SpeakerRecord(id="S3", label="Speaker 3"),
        ),
        merged_into=merged_into or {},
    )


def test_render_writes_the_transcript_with_names_merges_and_escaping() -> None:
    store = InMemoryUtterancesStore(UTTERANCES)

    result = RenderTranscript(store).run(MEETING, _state({"S3": "S1"}))

    assert result == Success("transcript.md")
    assert store.written == [
        (
            MEETING,
            (
                "[00:00:01] **Alex\\_:** first\n\n"
                "[00:01:05] **Alex\\_:** second\n\n"
                "[01:02:05] **Me:** third\n"
            ),
        )
    ]


def test_render_without_merges_uses_default_labels() -> None:
    store = InMemoryUtterancesStore(UTTERANCES)

    RenderTranscript(store).run(MEETING, _state())

    assert "**Speaker 3:** second" in store.written[0][1]


def test_render_of_a_meeting_without_speech_writes_the_no_speech_text() -> None:
    store = InMemoryUtterancesStore(())

    RenderTranscript(store).run(MEETING, _state())

    assert store.written == [(MEETING, "(no speech detected)\n")]


def test_a_load_failure_is_returned_and_nothing_is_written() -> None:
    error = TranscriptCorruptError("damaged")
    store = InMemoryUtterancesStore(load_failure=error)

    result = RenderTranscript(store).run(MEETING, _state())

    assert result == Failure(error)
    assert store.written == []


def test_a_write_failure_is_returned() -> None:
    error = TranscriptStorageError("disk")
    store = InMemoryUtterancesStore(UTTERANCES, write_failure=error)

    assert RenderTranscript(store).run(MEETING, _state()) == Failure(error)


def test_effective_utterances_are_served_as_data_in_time_order() -> None:
    store = InMemoryUtterancesStore(UTTERANCES)

    shown = RenderTranscript(store).effective_utterances(MEETING, _state({"S3": "S1"}))

    assert shown.unwrap() == (
        Utterance(speaker="S1", start=1.0, end=2.0, text="first"),
        Utterance(speaker="S1", start=65.0, end=70.0, text="second"),
        Utterance(speaker="ME", start=3725.0, end=3726.0, text="third"),
    )
    assert store.written == []
