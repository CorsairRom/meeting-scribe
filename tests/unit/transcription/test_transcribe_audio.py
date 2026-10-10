from pathlib import Path

import pytest
from returns.result import Failure, Success

from meeting_scribe.base.domain.errors import GpuOutOfMemoryError, InvalidValueError
from meeting_scribe.transcription.application.transcribe_audio import (
    TranscribeAudio,
    TranscriptionRequest,
)
from meeting_scribe.transcription.domain.errors import TranscriptionError
from meeting_scribe.transcription.domain.params import Device
from tests.unit.transcription.fakes import (
    FINGERPRINT,
    PARAMS,
    FakeTranscriber,
    InMemoryWordsCache,
    RecordingReporter,
)

MEETING = Path("/meetings/m1")


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _service(transcriber=None, cache=None, clock=None):
    transcriber = transcriber or FakeTranscriber()
    cache = cache or InMemoryWordsCache()
    return TranscribeAudio(transcriber, cache, clock or Clock()), transcriber, cache


def _request(device: Device = "cuda", loads: list[int] | None = None):
    def load():
        if loads is not None:
            loads.append(1)
        return Success(object())

    return TranscriptionRequest(
        params=PARAMS,
        device=device,
        fingerprint=FINGERPRINT,
        duration_seconds=60.0,
        load_audio=load,
    )


def test_a_miss_loads_the_model_transcribes_and_writes_the_cache() -> None:
    service, transcriber, cache = _service()
    reporter = RecordingReporter()

    outcome = service.run(MEETING, _request(), reporter).unwrap()

    assert (outcome.detected_language, outcome.cached, outcome.word_count) == ("es", False, 1)
    assert reporter.events[0] == ("started", "cuda/float16")
    assert len(transcriber.calls) == 1
    assert cache.document["data"]["words"][0]["text"] == "hola"  # type: ignore[index]


def test_a_valid_cache_skips_audio_and_model_entirely() -> None:
    service, transcriber, _ = _service()
    service.run(MEETING, _request(), RecordingReporter()).unwrap()
    loads: list[int] = []
    reporter = RecordingReporter()

    outcome = service.run(MEETING, _request(loads=loads), reporter).unwrap()

    assert outcome.cached is True
    assert loads == []
    assert len(transcriber.calls) == 1  # still only the first run
    assert reporter.events == [("cache_hit", "words.json")]


def test_changed_params_make_the_service_transcribe_again() -> None:
    service, transcriber, _ = _service()
    service.run(MEETING, _request(), RecordingReporter()).unwrap()
    request = _request().model_copy(
        update={"params": PARAMS.model_copy(update={"language": "auto"})}
    )

    service.run(MEETING, request, RecordingReporter()).unwrap()

    assert len(transcriber.calls) == 2


def test_progress_is_throttled_to_one_event_per_second() -> None:
    clock = Clock()
    transcriber = FakeTranscriber()
    transcriber.progress_at = (5.0, 6.0, 7.0)
    service, _, _ = _service(transcriber, clock=clock)
    reporter = RecordingReporter()
    original = transcriber.transcribe

    def ticking(audio, params, device, on_progress):
        def tick(seconds: float) -> None:
            clock.now += 0.6
            on_progress(seconds)

        return original(audio, params, device, tick)

    transcriber.transcribe = ticking  # type: ignore[method-assign]
    service.run(MEETING, _request(), reporter).unwrap()

    progress = [payload for name, payload in reporter.events if name == "progress"]
    assert progress == [(5.0, 60.0), (7.0, 60.0)]  # 0.6 s after the first is too soon


def test_errors_from_the_model_and_the_cache_are_returned_not_raised() -> None:
    transcriber = FakeTranscriber()
    transcriber.error = GpuOutOfMemoryError("out of memory")
    service, _, cache = _service(transcriber)

    failed = service.run(MEETING, _request(), RecordingReporter())

    assert isinstance(failed.failure(), GpuOutOfMemoryError)
    assert cache.document == {}  # nothing half-written

    healthy, _, broken = _service()
    broken.fail_store = True
    stored = healthy.run(MEETING, _request(), RecordingReporter())
    assert isinstance(stored.failure(), TranscriptionError)
    assert str(stored.failure()) == "disk full"  # the cache's own error, unchanged


def test_an_undecodable_recording_fails_before_the_model_and_writes_no_cache() -> None:
    service, transcriber, cache = _service()
    cache.document = {"stale": True}
    decode_error = InvalidValueError("unreadable chunk")
    request = _request().model_copy(update={"load_audio": lambda: Failure(decode_error)})
    reporter = RecordingReporter()

    failed = service.run(MEETING, request, reporter)

    assert failed.failure() is decode_error
    assert transcriber.calls == []
    assert cache.document == {"stale": True}  # untouched, not replaced
    assert [name for name, _ in reporter.events] == ["started"]


def test_the_cpu_run_is_labelled_and_logged_as_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    service, _, _ = _service()
    reporter = RecordingReporter()

    with caplog.at_level("WARNING"):
        service.run(MEETING, _request(device="cpu"), reporter).unwrap()

    assert reporter.events[0] == ("started", "cpu/int8")
    assert "CPU" in caplog.text
