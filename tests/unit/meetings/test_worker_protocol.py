import json

import pytest
from returns.result import Failure

from meeting_scribe.meetings.domain.errors import WorkerProtocolError
from meeting_scribe.meetings.domain.meeting import ProcessingParams
from meeting_scribe.meetings.domain.worker import (
    CacheHitEvent,
    ProgressEvent,
    ResultEvent,
    StageEvent,
    StartedEvent,
    WorkerErrorEvent,
    WorkerJob,
    parse_event,
)


def test_job_spec_is_protocol_v1_json_with_the_stage_params() -> None:
    params = ProcessingParams(transcription={"model": "tiny"}, speakers={"pipeline": "p"})

    job = WorkerJob.build("transcribe", "/m/1", "cuda", params)

    assert json.loads(job.to_json()) == {
        "protocol": 1,
        "stage": "transcribe",
        "meeting_dir": "/m/1",
        "device": "cuda",
        "transcription": {"model": "tiny"},
        "speakers": {"pipeline": "p"},
    }


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            '{"event":"started","pid":7,"device":"cuda/float16"}',
            StartedEvent(pid=7, device="cuda/float16"),
        ),
        ('{"event":"cache_hit","artifact":"words.json"}', CacheHitEvent(artifact="words.json")),
        (
            '{"event":"progress","stage":"transcribe","processed_seconds":1.5,"total_seconds":9}',
            ProgressEvent(stage="transcribe", processed_seconds=1.5, total_seconds=9.0),
        ),
        ('{"event":"stage","state":"EXTRACTING_SAMPLES"}', StageEvent(state="EXTRACTING_SAMPLES")),
        (
            '{"event":"error","error_code":"GPU_OUT_OF_MEMORY","message":"oom"}',
            WorkerErrorEvent(error_code="GPU_OUT_OF_MEMORY", message="oom"),
        ),
    ],
)
def test_parse_event_reads_each_protocol_event(line: str, expected: object) -> None:
    assert parse_event(line).unwrap() == expected


def test_parse_result_event_with_speakers_and_artifacts() -> None:
    line = json.dumps(
        {
            "event": "result",
            "artifacts": {"words": "words.json"},
            "speakers": [{"id": "S1", "label": "Speaker 1"}],
            "detected_language": "es",
            "audio": {"chunk_count": 1, "total_frames": 16000, "duration_seconds": 1.0},
        }
    )

    event = parse_event(line).unwrap()

    assert isinstance(event, ResultEvent)
    assert event.artifacts == {"words": "words.json"}
    assert event.speakers[0].id == "S1"
    assert event.detected_language == "es"


@pytest.mark.parametrize(
    "line",
    [
        "not json at all",
        "[1, 2]",
        '{"no_event_key": 1}',
        '{"event": "mystery"}',
        '{"event": "progress", "stage": "x"}',
        '{"event": "stage", "state": "DONE"}',
        "",
    ],
)
def test_parse_event_rejects_malformed_lines_as_protocol_errors(line: str) -> None:
    result = parse_event(line)

    assert isinstance(result, Failure)
    assert isinstance(result.failure(), WorkerProtocolError)
    assert result.failure().error_code == "WORKER_PROTOCOL_ERROR"
