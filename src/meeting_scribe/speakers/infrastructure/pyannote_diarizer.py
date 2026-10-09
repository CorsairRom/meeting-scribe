"""pyannote adapter: device policy, pipeline lifetime, progress and error mapping (spec 6.4).

It talks to the library only through a ``PyannoteBackend`` (the real one lives in
``pyannote_backend``), so this logic is tested with a fake backend and never loads a model.
Runs in the worker process only.
"""

import gc
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from typing import Protocol

from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.audio import SAMPLE_RATE, Samples
from meeting_scribe.base.domain.errors import (
    GpuOutOfMemoryError,
    GpuUnavailableError,
    MeetingScribeError,
    ModelUnavailableError,
)
from meeting_scribe.speakers.domain.diarization import (
    CUDA_REMEDY,
    Device,
    DiarizationResult,
    check_device_available,
)
from meeting_scribe.speakers.domain.errors import DiarizationError, DiarizationModelAccessError
from meeting_scribe.speakers.domain.params import DiarizationParams
from meeting_scribe.speakers.domain.turns import Turn

logger = logging.getLogger(__name__)

_NO_CUDA = (
    "no cuda-capable device",
    "driver version is insufficient",
    "unsupported device cuda",
    "cuda driver initialization failed",
    "found no nvidia driver",
)
_ACCESS_ERRORS = frozenset({"GatedRepoError", "RepositoryNotFoundError", "LocalTokenNotFoundError"})
"""Gated or private pipeline (``GatedRepoError`` extends ``RepositoryNotFoundError``) and
``token=True`` without a stored login (``LocalTokenNotFoundError``): all fixed by ``hf auth
login`` plus accepting the terms (assumption A-11)."""
_ACCESS_STATUS = frozenset({401, 403})
_MODEL_LOOKUP_ERRORS = frozenset(
    {"LocalEntryNotFoundError", "OfflineModeIsEnabled", "HFValidationError"}
)

# Shares of the progress bar: the two time-consuming pipeline steps (hook step names).
_SEGMENTATION_SHARE = 0.4
_EMBEDDINGS_SHARE = 0.55


class SegmentLike(Protocol):
    @property
    def start(self) -> float: ...

    @property
    def end(self) -> float: ...


class AnnotationLike(Protocol):
    def itertracks(self, *, yield_label: bool) -> Iterable[tuple[SegmentLike, object, str]]: ...


class OutputLike(Protocol):
    @property
    def speaker_diarization(self) -> AnnotationLike: ...

    @property
    def exclusive_speaker_diarization(self) -> AnnotationLike: ...


type Hook = Callable[..., None]


class PipelineLike(Protocol):
    def __call__(
        self,
        file: Mapping[str, object],
        *,
        num_speakers: int | None,
        min_speakers: int | None,
        max_speakers: int | None,
        hook: Hook,
    ) -> OutputLike: ...


class PyannoteBackend(Protocol):
    def prepare(self) -> None:
        """Opt out of telemetry and make the libraries loadable, before the first import."""
        ...

    def cuda_device_count(self) -> int: ...

    def load_pipeline(self, pipeline: str, device: Device) -> PipelineLike:
        """``Pipeline.from_pretrained(pipeline, token=True)`` moved to ``device``."""
        ...

    def make_input(self, audio: Samples) -> Mapping[str, object]:
        """The in-memory pyannote ``AudioFile`` for a mono 16 kHz signal."""
        ...

    def engine_versions(self) -> dict[str, str]: ...


class PyannoteDiarizer:
    def __init__(self, backend: PyannoteBackend) -> None:
        self._backend = backend

    def engine_versions(self) -> dict[str, str]:
        return self._backend.engine_versions()

    def diarize(
        self,
        audio: Samples,
        params: DiarizationParams,
        device: Device,
        on_progress: Callable[[float], None],
    ) -> Result[DiarizationResult, MeetingScribeError]:
        pipeline = None
        try:
            self._backend.prepare()
            count = self._backend.cuda_device_count() if device == "cuda" else 0
            available = check_device_available(device, count)
            if isinstance(available, Failure):
                return available
            started = time.monotonic()
            pipeline = self._backend.load_pipeline(params.pipeline, device)
            logger.info(
                "Pipeline '%s' loaded on %s in %.1f s.",
                params.pipeline,
                device,
                time.monotonic() - started,
            )
            return Success(self._run(pipeline, audio, params, on_progress))
        except Exception as error:
            return Failure(map_pyannote_error(error, params.pipeline))
        finally:
            del pipeline
            gc.collect()  # the process exit is what really frees the GPU (ADR-007)

    def _run(
        self,
        pipeline: PipelineLike,
        audio: Samples,
        params: DiarizationParams,
        on_progress: Callable[[float], None],
    ) -> DiarizationResult:
        started = time.monotonic()
        output = pipeline(
            self._backend.make_input(audio),
            num_speakers=params.num_speakers,
            min_speakers=params.min_speakers,
            max_speakers=params.max_speakers,
            hook=progress_hook(on_progress),
        )
        elapsed = time.monotonic() - started
        seconds = len(audio) / SAMPLE_RATE
        logger.info(
            "Diarized %.0f s of audio in %.1f s (%.2fx real time).",
            seconds,
            elapsed,
            elapsed / seconds if seconds else 0.0,
        )
        return DiarizationResult(
            turns=_turns(output.speaker_diarization),
            exclusive=_turns(output.exclusive_speaker_diarization),
        )


def _turns(annotation: AnnotationLike) -> tuple[Turn, ...]:
    found = (
        Turn(start=float(segment.start), end=float(segment.end), speaker=str(label))
        for segment, _, label in annotation.itertracks(yield_label=True)
    )
    return tuple(sorted(found, key=lambda turn: (turn.start, turn.end)))


def progress_hook(on_progress: Callable[[float], None]) -> Hook:
    """A pipeline ``hook`` that reports a 0..1 fraction and prints nothing (assumption A-12).

    pyannote calls ``hook(step_name, artifact, file=..., total=..., completed=...)`` after each
    major step, and repeatedly with ``total``/``completed`` during segmentation and embeddings.
    """

    def hook(
        step_name: str,
        artifact: object = None,  # noqa: ARG001
        *,
        total: int | None = None,
        completed: int | None = None,
        **_: object,
    ) -> None:
        within = completed / total if total and completed is not None else 0.0
        if step_name == "segmentation":
            on_progress(_SEGMENTATION_SHARE * min(within, 1.0))
        elif step_name == "embeddings":
            on_progress(_SEGMENTATION_SHARE + _EMBEDDINGS_SHARE * min(within, 1.0))
        elif step_name == "discrete_diarization":
            on_progress(1.0)

    return hook


def map_pyannote_error(error: Exception, pipeline: str) -> MeetingScribeError:
    """Typed failure for a library exception; only real CUDA absence maps to ``GPU_UNAVAILABLE``.

    Messages never repeat the library's text for access and lookup errors (it can echo request
    details), and the Hugging Face token is never read here.
    """
    message = str(error)
    lowered = message.lower()
    names = {cls.__name__ for cls in type(error).__mro__}
    if "OutOfMemoryError" in names or "out of memory" in lowered:
        return GpuOutOfMemoryError(f"The GPU ran out of memory: {message}")
    if any(marker in lowered for marker in _NO_CUDA):
        return GpuUnavailableError(f"CUDA is not usable ({message}). {CUDA_REMEDY}")
    status = getattr(getattr(error, "response", None), "status_code", None)
    if names & _ACCESS_ERRORS or status in _ACCESS_STATUS:
        return DiarizationModelAccessError(
            f"Cannot access the diarization model '{pipeline}' ({type(error).__name__}). "
            f"Accept its terms at https://hf.co/{pipeline} and run `hf auth login` once in a "
            "terminal, then reprocess the meeting (the transcription is kept)."
        )
    if names & _MODEL_LOOKUP_ERRORS:
        return ModelUnavailableError(
            f"The pipeline '{pipeline}' is not in the Hugging Face cache and could not be "
            f"downloaded ({type(error).__name__}). Check the pipeline name and the network."
        )
    if isinstance(error, ImportError):
        return DiarizationError(
            f"pyannote.audio is not installed in this environment ({message}); "
            "install the ml extra (uv sync --extra ml)."
        )
    return DiarizationError(f"{type(error).__name__}: {message}")
