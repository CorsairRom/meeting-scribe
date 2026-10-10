from pathlib import Path
from typing import cast

from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.audio import Samples
from meeting_scribe.base.domain.errors import GpuOutOfMemoryError, MeetingScribeError
from meeting_scribe.meetings.domain.errors import MeetingStorageError
from meeting_scribe.speakers.application.diarize_audio import DiarizationRequest, DiarizeAudio
from meeting_scribe.speakers.domain.diarization import TURNS_FILE
from meeting_scribe.speakers.domain.me_detection import DiarizedChannel
from tests.unit.speakers.envelopes import SPEECH, envelope
from tests.unit.speakers.fakes import (
    FINGERPRINT,
    PARAMS,
    RESULT,
    FakeDiarizer,
    InMemoryTurnsCache,
    RecordingReporter,
)

MEETING = Path("/meetings/m1")
SPOKEN = envelope(600, [(10, 200, SPEECH)])  # the system channel has speech
SILENT = envelope(600)  # in person: nothing on the system channel


class Stage:
    def __init__(self, system: list[float] = SPOKEN) -> None:
        self.diarizer = FakeDiarizer()
        self.cache = InMemoryTurnsCache()
        self.reporter = RecordingReporter()
        self.loads: list[DiarizedChannel] = []
        self.system = system
        self.load_error: MeetingScribeError | None = None
        self.now = 0.0

    def _load(self, channel: DiarizedChannel) -> Result[Samples, MeetingScribeError]:
        self.loads.append(channel)
        return Failure(self.load_error) if self.load_error else Success(cast("Samples", object()))

    def request(self, **changes: object) -> DiarizationRequest:
        values: dict[str, object] = {
            "params": PARAMS,
            "device": "cuda",
            "fingerprint": FINGERPRINT,
            "duration_seconds": 60.0,
            "system_energies": self.system,
            "load_channel": self._load,
        }
        return DiarizationRequest.model_validate(values | changes)

    def run(self, **changes: object):
        service = DiarizeAudio(self.diarizer, self.cache, monotonic=lambda: self.now)
        return service.run(MEETING, self.request(**changes), self.reporter)


def test_a_miss_diarizes_the_system_channel_and_writes_turns_json() -> None:
    stage = Stage()

    outcome = stage.run().unwrap()

    assert (outcome.artifact, outcome.channel, outcome.cached) == (TURNS_FILE, "system", False)
    assert outcome.result == RESULT
    assert stage.loads == ["system"]
    assert stage.reporter.events == [("started", "cuda")]
    document = stage.cache.document
    assert document["cache_version"] == 1
    assert document["key"]["params"]["channel"] == "system"  # type: ignore[index]


def test_a_second_run_with_the_same_key_never_loads_audio_or_the_model() -> None:
    stage = Stage()
    stage.run()
    stage.diarizer.calls.clear()
    stage.loads.clear()
    stage.reporter.events.clear()

    outcome = stage.run().unwrap()

    assert outcome.cached and outcome.result == RESULT
    assert stage.diarizer.calls == [] and stage.loads == []
    assert stage.reporter.events == [("cache_hit", TURNS_FILE)]


def test_every_key_field_invalidates_the_cache() -> None:
    changes = [
        {"params": PARAMS.model_copy(update={"pipeline": "pyannote/other"})},
        {"params": PARAMS.model_copy(update={"num_speakers": 2})},
        {"params": PARAMS.model_copy(update={"min_speakers": 2})},
        {"params": PARAMS.model_copy(update={"max_speakers": 5})},
        {"device": "cpu"},
        {"fingerprint": FINGERPRINT[:1]},
    ]
    for change in changes:
        stage = Stage()
        stage.run()
        stage.diarizer.calls.clear()

        assert stage.run(**change).unwrap().cached is False, change
        assert len(stage.diarizer.calls) == 1, change


def test_the_engine_version_and_the_channel_invalidate_the_cache() -> None:
    stage = Stage()
    stage.run()
    stage.diarizer.versions["pyannote.audio"] = "4.1.0"
    assert stage.run().unwrap().cached is False

    stage.system = SILENT  # the same meeting judged in person: another channel
    outcome = stage.run(system_energies=SILENT).unwrap()

    assert outcome.cached is False and outcome.channel == "mic"
    assert stage.loads[-1] == "mic"


def test_the_noise_margin_matters_only_through_the_channel_it_picks() -> None:
    stage = Stage()
    stage.run()

    quieter = PARAMS.model_copy(update={"me_noise_margin_db": 12.0})

    assert stage.run(params=quieter).unwrap().cached is True  # same channel, same key


def test_a_damaged_cache_is_a_miss() -> None:
    stage = Stage()
    stage.run()
    stage.cache.document["data"] = {"diarization": "nope"}

    assert stage.run().unwrap().cached is False


def test_failures_are_returned_and_nothing_is_cached() -> None:
    stage = Stage()
    stage.diarizer.error = GpuOutOfMemoryError("out of memory")
    assert isinstance(stage.run().failure(), GpuOutOfMemoryError)
    assert stage.cache.document == {}

    stage = Stage()
    stage.load_error = MeetingStorageError("cannot read")
    assert isinstance(stage.run().failure(), MeetingStorageError)
    assert stage.diarizer.calls == []

    stage = Stage()
    stage.cache.fail_store = True
    assert stage.run().failure().error_code == "DIARIZATION_FAILED"


def test_progress_is_throttled_to_one_event_per_second_and_scaled_to_the_audio() -> None:
    stage = Stage()
    ticks = iter([0.0, 0.2, 0.9, 1.1, 2.5])
    stage.diarizer.progress_at = (0.1, 0.2, 0.3, 0.5, 1.5)
    service = DiarizeAudio(stage.diarizer, stage.cache, monotonic=lambda: next(ticks))

    service.run(MEETING, stage.request(), stage.reporter)

    assert [e for e in stage.reporter.events if e[0] == "progress"] == [
        ("progress", (6.0, 60.0)),
        ("progress", (30.0, 60.0)),
        ("progress", (60.0, 60.0)),  # a fraction above 1 is clamped
    ]


def test_the_final_update_is_never_dropped_by_the_throttle() -> None:
    stage = Stage()
    ticks = iter([0.0, 0.1])  # the terminal update arrives inside the throttle window
    stage.diarizer.progress_at = (0.9, 1.0)
    service = DiarizeAudio(stage.diarizer, stage.cache, monotonic=lambda: next(ticks))

    service.run(MEETING, stage.request(), stage.reporter)

    assert [e for e in stage.reporter.events if e[0] == "progress"] == [
        ("progress", (54.0, 60.0)),
        ("progress", (60.0, 60.0)),
    ]


def test_an_invalid_speakers_spec_is_an_invalid_value_error() -> None:
    result = DiarizationRequest.from_spec(
        {"pipeline": ""}, "cuda", FINGERPRINT, 1.0, [], lambda c: Success(cast("Samples", c))
    )

    assert result.failure().error_code == "INVALID_VALUE"
    assert "params.pipeline" in str(result.failure())
