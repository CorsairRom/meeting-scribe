from pathlib import Path
from typing import cast

import pytest
from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.audio import Samples
from meeting_scribe.base.domain.errors import GpuOutOfMemoryError
from meeting_scribe.meetings.application.transcribe_stage import run_transcribe_stage
from meeting_scribe.meetings.domain.errors import MeetingStorageError
from meeting_scribe.meetings.domain.meeting import ProcessingParams
from meeting_scribe.meetings.domain.timeline import Timeline
from meeting_scribe.meetings.domain.worker import (
    CacheHitEvent,
    ProgressEvent,
    StartedEvent,
    WorkerEvent,
    WorkerJob,
)
from meeting_scribe.transcription.application.transcribe_audio import TranscribeAudio
from tests.unit.meetings.fakes import FakeAudioInspector
from tests.unit.transcription.fakes import PARAMS, FakeTranscriber, InMemoryWordsCache

MEETING = Path("/meetings/m1")


class FakeMix:
    def __init__(self) -> None:
        self.reads = 0

    def read_mix(
        self, meeting_dir: Path, timeline: Timeline
    ) -> Result[Samples, MeetingStorageError]:
        self.reads += 1
        return Success(cast("Samples", object()))


def _job(device: str = "cuda", spec: dict | None = None) -> WorkerJob:
    params = ProcessingParams(transcription=spec if spec is not None else PARAMS.model_dump())
    return WorkerJob.build("transcribe", str(MEETING), device, params)


class Stage:
    def __init__(self) -> None:
        self.transcriber = FakeTranscriber()
        self.cache = InMemoryWordsCache()
        self.mix = FakeMix()
        self.events: list[WorkerEvent] = []
        self.inspector = FakeAudioInspector()

    def run(self, job: WorkerJob):
        return run_transcribe_stage(
            job,
            audio=self.inspector,
            mix=self.mix,
            transcribe=TranscribeAudio(self.transcriber, self.cache),
            emit=self.events.append,
        )


def test_a_miss_emits_started_and_returns_the_artifact_language_and_audio() -> None:
    stage = Stage()

    result = stage.run(_job()).unwrap()

    assert result.artifacts == {"words": "words.json"}
    assert result.detected_language == "es"
    assert result.audio is not None and result.audio.total_frames == 480_640
    assert [type(e) for e in stage.events] == [StartedEvent]
    assert stage.events[0].device == "cuda/float16"  # type: ignore[union-attr]
    assert stage.mix.reads == 1


def test_a_second_run_hits_the_cache_without_decoding_or_a_model() -> None:
    stage = Stage()
    stage.run(_job()).unwrap()
    stage.events.clear()

    again = stage.run(_job()).unwrap()

    assert [type(e) for e in stage.events] == [CacheHitEvent]
    assert again.artifacts == {"words": "words.json"}
    assert stage.mix.reads == 1
    assert len(stage.transcriber.calls) == 1


def test_progress_reaches_the_event_stream() -> None:
    stage = Stage()
    stage.transcriber.progress_at = (30.0,)

    stage.run(_job()).unwrap()

    progress = [e for e in stage.events if isinstance(e, ProgressEvent)]
    assert [(p.stage, p.processed_seconds) for p in progress] == [("transcribe", 30.0)]


def test_missing_audio_and_model_errors_come_back_as_failures() -> None:
    stage = Stage()
    stage.inspector.missing.add(MEETING.name)
    assert stage.run(_job()).failure().error_code == "MEETING_AUDIO_MISSING"  # type: ignore[union-attr]

    other = Stage()
    other.transcriber.error = GpuOutOfMemoryError("oom")
    assert isinstance(other.run(_job()).failure(), GpuOutOfMemoryError)


@pytest.mark.parametrize(
    "job",
    [_job(spec={}), _job(spec={**PARAMS.model_dump(), "beam_size": 0}), _job(device="tpu")],
)
def test_an_invalid_job_spec_is_an_invalid_value_error_not_a_crash(job: WorkerJob) -> None:
    stage = Stage()

    result = stage.run(job)

    assert isinstance(result, Failure)
    assert result.failure().error_code == "INVALID_VALUE"
    assert stage.events == []
