"""faster-whisper adapter: device policy, model lifetime and error mapping (spec 6.4).

It talks to the library only through a ``WhisperBackend`` (the real one lives in
``faster_whisper_backend``), so this logic is tested with a fake backend and never loads a model.
Runs in the worker process only.
"""

import gc
import logging
import time
from collections.abc import Callable, Iterable, Sequence
from typing import Protocol

from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.audio import SAMPLE_RATE, Samples
from meeting_scribe.base.domain.errors import (
    GpuOutOfMemoryError,
    GpuUnavailableError,
    MeetingScribeError,
    ModelUnavailableError,
)
from meeting_scribe.transcription.domain.errors import TranscriptionError
from meeting_scribe.transcription.domain.params import (
    Device,
    TranscriptionParams,
    check_device_available,
)
from meeting_scribe.transcription.domain.words import Segment, TranscriptionResult, Word

logger = logging.getLogger(__name__)

_NO_CUDA = (
    "no cuda-capable device",
    "driver version is insufficient",
    "unsupported device cuda",
    "cuda driver",
)
_MODEL_LOOKUP_ERRORS = frozenset(
    {
        "LocalEntryNotFoundError",
        "RepositoryNotFoundError",
        "GatedRepoError",
        "OfflineModeIsEnabled",
        "HFValidationError",
    }
)


class WordLike(Protocol):
    @property
    def word(self) -> str: ...

    @property
    def start(self) -> float: ...

    @property
    def end(self) -> float: ...

    @property
    def probability(self) -> float: ...


class SegmentLike(Protocol):
    @property
    def start(self) -> float: ...

    @property
    def end(self) -> float: ...

    @property
    def text(self) -> str: ...

    @property
    def words(self) -> Sequence[WordLike] | None: ...


class InfoLike(Protocol):
    @property
    def language(self) -> str: ...


class ModelLike(Protocol):
    def transcribe(
        self,
        audio: Samples,
        *,
        language: str | None,
        beam_size: int,
        vad_filter: bool,
        condition_on_previous_text: bool,
        word_timestamps: bool,
    ) -> tuple[Iterable[SegmentLike], InfoLike]: ...


class WhisperBackend(Protocol):
    def prepare(self, device: Device) -> None:
        """Make the libraries loadable (CUDA preload) before the first import."""
        ...

    def cuda_device_count(self) -> int: ...

    def load_model(self, model: str, device: Device, compute_type: str) -> ModelLike: ...

    def engine_versions(self) -> dict[str, str]: ...


class FasterWhisperTranscriber:
    def __init__(self, backend: WhisperBackend) -> None:
        self._backend = backend

    def engine_versions(self) -> dict[str, str]:
        return self._backend.engine_versions()

    def transcribe(
        self,
        audio: Samples,
        params: TranscriptionParams,
        device: Device,
        on_progress: Callable[[float], None],
    ) -> Result[TranscriptionResult, MeetingScribeError]:
        model = None
        try:
            self._backend.prepare(device)
            count = self._backend.cuda_device_count() if device == "cuda" else 0
            available = check_device_available(device, count)
            if isinstance(available, Failure):
                return available
            started = time.monotonic()
            model = self._backend.load_model(
                params.model, device, params.effective_compute_type(device)
            )
            logger.info(
                "Model '%s' loaded on %s in %.1f s.",
                params.model,
                params.device_label(device),
                time.monotonic() - started,
            )
            return Success(self._decode(model, audio, params, on_progress))
        except Exception as error:
            return Failure(map_whisper_error(error, params.model))
        finally:
            del model
            gc.collect()  # the process exit is what really frees the GPU (ADR-007)

    @staticmethod
    def _decode(
        model: ModelLike,
        audio: Samples,
        params: TranscriptionParams,
        on_progress: Callable[[float], None],
    ) -> TranscriptionResult:
        started = time.monotonic()
        segments, info = model.transcribe(
            audio,
            language=params.whisper_language(),
            beam_size=params.beam_size,
            vad_filter=params.vad_filter,
            condition_on_previous_text=params.condition_on_previous_text,
            word_timestamps=True,
        )
        collected: list[Segment] = []
        for segment in segments:  # the generator does the work as it is iterated
            words = tuple(
                Word(start=w.start, end=w.end, text=w.word.strip(), probability=w.probability)
                for w in segment.words or ()
                if w.word.strip()
            )
            collected.append(
                Segment(start=segment.start, end=segment.end, text=segment.text, words=words)
            )
            on_progress(segment.end)
        elapsed = time.monotonic() - started
        seconds = len(audio) / SAMPLE_RATE
        logger.info(
            "Transcribed %.0f s of audio in %.1f s (%.2fx real time).",
            seconds,
            elapsed,
            elapsed / seconds if seconds else 0.0,
        )
        return TranscriptionResult.from_segments(info.language, collected)


def map_whisper_error(error: Exception, model: str) -> MeetingScribeError:
    """Typed failure for a library exception; only real CUDA absence maps to ``GPU_UNAVAILABLE``.

    The model failure message never repeats the library's text, which can echo request details.
    """
    message = str(error)
    lowered = message.lower()
    if "out of memory" in lowered:
        return GpuOutOfMemoryError(f"The GPU ran out of memory: {message}")
    if any(marker in lowered for marker in _NO_CUDA):
        return GpuUnavailableError(
            f"CUDA is not usable ({message}). Fix the NVIDIA driver, or set "
            "PROCESSING_DEVICE=cpu to run on the CPU explicitly (slow)."
        )
    names = {cls.__name__ for cls in type(error).__mro__}
    if names & _MODEL_LOOKUP_ERRORS or (
        isinstance(error, ValueError) and "invalid model size" in lowered
    ):
        return ModelUnavailableError(
            f"The model '{model}' is not in the Hugging Face cache and could not be downloaded "
            f"({type(error).__name__}). Check the model name and the network."
        )
    if isinstance(error, ImportError):
        return TranscriptionError(
            f"faster-whisper is not installed in this environment ({message}); "
            "install the ml extra (uv sync --extra ml)."
        )
    return TranscriptionError(f"{type(error).__name__}: {message}")
