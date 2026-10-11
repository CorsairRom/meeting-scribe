"""The review members of ``MeetingsInterface``: row shapes, error mapping, signal emission."""

import asyncio
from dataclasses import dataclass

import pytest
from dbus_fast import DBusError, Variant

from meeting_scribe.meetings.application.event_hub import ProcessingEventHub
from meeting_scribe.meetings.application.processing_controller import (
    ProcessingConfig,
    ProcessingController,
)
from meeting_scribe.meetings.application.review_controller import ReviewController
from meeting_scribe.meetings.domain.meeting import (
    Meeting,
    ProcessingParams,
    SpeakerEntry,
    SpeakerReview,
)
from meeting_scribe.meetings.infrastructure.dbus.meetings_interface import MeetingsInterface
from meeting_scribe.speakers.application.render_transcript import RenderTranscript, Utterance
from meeting_scribe.speakers.application.review_speakers import ReviewSpeakers
from tests.unit.meetings.fakes import (
    RECORDING,
    ROOT,
    T0,
    FakeAudioInspector,
    FakeClock,
    FakeMeetingLock,
    FakeSampleReader,
    FakeUtterancesStore,
    InMemoryMeetingRepository,
    ScriptedRunner,
)

SPEAKERS = (
    SpeakerEntry(id="ME", label="Me", is_me=True, talk_seconds=5.0, utterance_count=1),
    SpeakerEntry(
        id="S1", label="Speaker 1", talk_seconds=30.0, utterance_count=3, sample="samples/S1.wav"
    ),
    SpeakerEntry(id="S2", label="Speaker 2", talk_seconds=20.0, utterance_count=2),
)


@dataclass
class Harness:
    interface: MeetingsInterface
    repository: InMemoryMeetingRepository
    samples: FakeSampleReader
    changed: list[str]


def _harness(monkeypatch: pytest.MonkeyPatch) -> Harness:
    repository = InMemoryMeetingRepository()
    lock = FakeMeetingLock()
    repository.lock = lock
    store = FakeUtterancesStore(repository, lock)
    store.utterances = (
        Utterance(speaker="S1", start=0.0, end=2.0, text="Fake line one."),
        Utterance(speaker="S2", start=2.0, end=4.5, text="Fake line two."),
    )
    samples = FakeSampleReader(lock)
    samples.files["samples/S1.wav"] = b"RIFF-fake"
    meeting = (
        Meeting.enqueue("m1", ROOT / "m1", RECORDING, T0)
        .start(T0, ProcessingParams())
        .unwrap()
        .transcription_done()
        .unwrap()
        .diarization_done()
        .unwrap()
        .complete(T0, SPEAKERS, {}, "en")
        .unwrap()
    )
    repository.meetings["m1"] = meeting.with_speaker_review(SPEAKERS, SpeakerReview())
    review = ReviewController(repository, lock, ReviewSpeakers(), RenderTranscript(store), samples)
    interface = MeetingsInterface(_processing(repository), review)
    changed: list[str] = []
    monkeypatch.setattr(interface, "_emit_speakers_changed", changed.append)
    return Harness(interface, repository, samples, changed)


def _processing(repository: InMemoryMeetingRepository) -> ProcessingController:
    return ProcessingController(
        repository=repository,
        runner=ScriptedRunner(),
        audio=FakeAudioInspector(),
        clock=FakeClock(),
        meetings_dir=ROOT,
        config=ProcessingConfig(
            device="cpu",
            worker_timeout_factor=1.0,
            worker_min_timeout_seconds=900.0,
            params=ProcessingParams(),
            storage_retry_delays_seconds=(1.0,),
        ),
        events=ProcessingEventHub(),
    )


async def _error(call) -> str:
    with pytest.raises(DBusError) as excinfo:
        await call
    return excinfo.value.type


def test_get_speakers_rows_carry_typed_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        harness = _harness(monkeypatch)
        rows = await harness.interface.get_speakers("m1")
        assert [row["id"].value for row in rows] == ["ME", "S1", "S2"]
        first = rows[1]
        assert {key: (variant.signature, variant.value) for key, variant in first.items()} == {
            "id": ("s", "S1"),
            "label": ("s", "Speaker 1"),
            "name": ("s", ""),
            "display": ("s", "Speaker 1"),
            "is_me": ("b", False),
            "talk_seconds": ("d", 30.0),
            "utterance_count": ("i", 3),
            "has_sample": ("b", True),
            "merged_into": ("s", ""),
        }
        assert all(isinstance(v, Variant) for v in first.values())

    asyncio.run(scenario())


def test_get_transcript_rows_and_sample_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        harness = _harness(monkeypatch)
        assert await harness.interface.get_transcript("m1") == [
            ("S1", 0.0, 2.0, "Fake line one."),
            ("S2", 2.0, 4.5, "Fake line two."),
        ]
        assert await harness.interface.get_speaker_sample("m1", "S1") == b"RIFF-fake"

    asyncio.run(scenario())


def test_edits_emit_the_signal_once_and_a_noop_emits_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        harness = _harness(monkeypatch)
        interface = harness.interface
        renamed = await interface.rename_speaker("m1", "S1", "  Fake Name  ")
        assert renamed[1]["name"].value == "Fake Name"
        assert harness.changed == ["m1"]
        merged = await interface.merge_speakers("m1", "S2", "S1")
        assert merged[-1]["merged_into"].value == "S1"
        assert harness.changed == ["m1", "m1"]
        await interface.unmerge_speaker("m1", "S2")
        assert harness.changed == ["m1", "m1", "m1"]
        await interface.unmerge_speaker("m1", "S2")  # not merged: no change, no signal
        assert harness.changed == ["m1", "m1", "m1"]

    asyncio.run(scenario())


def test_failures_become_named_errors_that_never_quote_a_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        harness = _harness(monkeypatch)
        interface = harness.interface
        prefix = "dev.meetingscribe.Error."
        await interface.rename_speaker("m1", "S1", "Fake Name")
        assert await _error(interface.rename_speaker("m1", "S2", "fake name")) == (
            prefix + "SPEAKER_NAME_TAKEN"
        )
        assert await _error(interface.merge_speakers("m1", "S1", "S1")) == (
            prefix + "SPEAKER_MERGE_INVALID"
        )
        assert await _error(interface.get_speakers("nope")) == prefix + "ENTITY_NOT_FOUND_MEETING"
        assert await _error(interface.get_speaker_sample("m1", "S2")) == (
            prefix + "ENTITY_NOT_FOUND_SPEAKER_SAMPLE"
        )
        assert await _error(interface.get_speaker_sample("m1", "../x")) == (
            prefix + "ENTITY_NOT_FOUND_SPEAKER"
        )
        with pytest.raises(DBusError) as excinfo:
            await interface.rename_speaker("m1", "S2", "FAKE NAME")
        assert "fake name" not in excinfo.value.text.lower()
        assert harness.changed == ["m1"]  # failures emit nothing

    asyncio.run(scenario())


def test_a_job_that_ends_done_announces_a_speaker_change(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        harness = _harness(monkeypatch)
        done = harness.repository.meetings["m1"]
        harness.interface.finished(done)
        await asyncio.sleep(0)
        assert harness.changed == ["m1"]

    asyncio.run(scenario())
