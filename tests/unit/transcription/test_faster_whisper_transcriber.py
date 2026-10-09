from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import cast

import pytest

from meeting_scribe.base.domain.audio import Samples
from meeting_scribe.base.domain.errors import (
    GpuOutOfMemoryError,
    GpuUnavailableError,
    ModelUnavailableError,
)
from meeting_scribe.transcription.domain.errors import TranscriptionError
from meeting_scribe.transcription.domain.params import Device
from meeting_scribe.transcription.infrastructure.faster_whisper_transcriber import (
    FasterWhisperTranscriber,
    map_whisper_error,
)
from tests.unit.transcription.fakes import PARAMS


@dataclass
class W:
    word: str
    start: float
    end: float
    probability: float = 0.9


@dataclass
class S:
    start: float
    end: float
    text: str
    words: list[W] | None


@dataclass
class Info:
    language: str = "es"
    duration: float = 12.0


class FakeModel:
    def __init__(self, segments: list[S], error: Exception | None = None) -> None:
        self.segments = segments
        self.error = error
        self.options: dict[str, object] = {}

    def transcribe(
        self,
        audio: Samples,
        *,
        language: str | None,
        beam_size: int,
        vad_filter: bool,
        condition_on_previous_text: bool,
        word_timestamps: bool,
    ) -> tuple[Iterable[S], Info]:
        self.options = {
            "language": language,
            "beam_size": beam_size,
            "vad_filter": vad_filter,
            "condition_on_previous_text": condition_on_previous_text,
            "word_timestamps": word_timestamps,
        }
        if self.error:
            raise self.error
        return iter(self.segments), Info()


@dataclass
class FakeBackend:
    model: FakeModel = field(default_factory=lambda: FakeModel([]))
    cuda_devices: int = 1
    load_error: Exception | None = None
    calls: list[tuple[str, str, str]] = field(default_factory=list)
    prepared: list[str] = field(default_factory=list)

    def prepare(self, device: Device) -> None:
        self.prepared.append(device)

    def cuda_device_count(self) -> int:
        return self.cuda_devices

    def load_model(self, model: str, device: Device, compute_type: str) -> FakeModel:
        self.calls.append((model, device, compute_type))
        if self.load_error:
            raise self.load_error
        return self.model

    def engine_versions(self) -> dict[str, str]:
        return {"faster-whisper": "9.9.9"}


AUDIO = cast("Samples", [0.0] * 16_000)  # a real run passes a numpy array
SEGMENTS = [
    S(0.0, 2.0, " hola a todos", [W(" hola", 0.1, 0.5), W(" a", 0.6, 0.7), W(" ", 0.8, 0.8)]),
    S(2.0, 4.0, " silencio", None),
    S(4.0, 6.0, " adios", [W(" adios", 4.1, 4.9)]),
]


def _run(backend: FakeBackend, device: Device = "cuda", progress: list[float] | None = None):
    sink = progress if progress is not None else []
    return FasterWhisperTranscriber(backend).transcribe(AUDIO, PARAMS, device, sink.append)


def test_words_come_from_every_segment_with_trimmed_text_and_the_detected_language() -> None:
    backend = FakeBackend(FakeModel(SEGMENTS))
    progress: list[float] = []

    result = _run(backend, progress=progress).unwrap()

    assert [w.text for w in result.words] == ["hola", "a", "adios"]  # blank words are dropped
    assert (result.words[0].start, result.words[0].probability) == (0.1, 0.9)
    assert result.detected_language == "es"
    assert progress == [2.0, 4.0, 6.0]


def test_the_model_gets_the_configured_options_and_the_cuda_compute_type() -> None:
    backend = FakeBackend(FakeModel([]))

    _run(backend)

    assert backend.calls == [("large-v3-turbo", "cuda", "float16")]
    assert backend.prepared == ["cuda"]
    assert backend.model.options == {
        "language": "es",
        "beam_size": 5,
        "vad_filter": True,
        "condition_on_previous_text": False,
        "word_timestamps": True,
    }


def test_auto_language_passes_none_so_whisper_detects_it() -> None:
    backend = FakeBackend(FakeModel([]))
    params = PARAMS.model_validate({**PARAMS.model_dump(), "language": "auto"})

    FasterWhisperTranscriber(backend).transcribe(AUDIO, params, "cuda", lambda _: None)

    assert backend.model.options["language"] is None


def test_cuda_without_a_device_fails_and_never_loads_a_model_or_touches_the_cpu() -> None:
    backend = FakeBackend(cuda_devices=0)

    failure = _run(backend).failure()

    assert isinstance(failure, GpuUnavailableError)
    assert backend.calls == []


def test_cpu_is_explicit_and_uses_the_cpu_compute_type() -> None:
    backend = FakeBackend(cuda_devices=0)

    _run(backend, device="cpu").unwrap()

    assert backend.calls == [("large-v3-turbo", "cpu", "int8")]
    assert backend.prepared == ["cpu"]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RuntimeError("CUDA failed with error out of memory"), GpuOutOfMemoryError),
        (
            RuntimeError("CUDA failed with error no CUDA-capable device is detected"),
            GpuUnavailableError,
        ),
        (
            RuntimeError("CUDA driver version is insufficient for CUDA runtime version"),
            GpuUnavailableError,
        ),
        (type("LocalEntryNotFoundError", (OSError,), {})("offline"), ModelUnavailableError),
        (ValueError("Invalid model size 'nope', expected one of: tiny"), ModelUnavailableError),
        (ImportError("No module named 'faster_whisper'"), TranscriptionError),
        (RuntimeError("Library libcudnn_ops.so.9 is not found"), TranscriptionError),
    ],
)
def test_errors_map_to_specific_codes_and_never_to_a_cpu_retry(
    error: Exception, expected: type[Exception]
) -> None:
    mapped = map_whisper_error(error, "large-v3-turbo")

    assert type(mapped) is expected


def test_a_failure_while_loading_or_decoding_is_returned_not_raised() -> None:
    loading = _run(FakeBackend(load_error=RuntimeError("CUDA failed with error out of memory")))
    decoding = _run(FakeBackend(FakeModel([], error=RuntimeError("boom"))))

    assert isinstance(loading.failure(), GpuOutOfMemoryError)
    assert isinstance(decoding.failure(), TranscriptionError)
    assert "boom" in str(decoding.failure())


def test_a_model_error_message_names_the_model_but_cannot_carry_a_token() -> None:
    mapped = map_whisper_error(
        type("RepositoryNotFoundError", (Exception,), {})("401 hf_secret"), "x/y"
    )

    assert isinstance(mapped, ModelUnavailableError)
    assert "x/y" in str(mapped)
    assert "hf_secret" not in str(mapped)
