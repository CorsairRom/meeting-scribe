"""Worker process entry point: ``python -m meeting_scribe.worker <stage>`` (spec 6.1, ADR-007).

A second composition root next to ``main``: it reads the job spec from stdin, builds the stage's
adapters, runs one stage and speaks the protocol on the event stream. It reads no settings and no
``.env``; everything comes in the job. Importing this module loads no ML library, and ``main``
validates the stage and the job before it builds anything: only the requested stage is built,
inside the guard that turns a crash into one protocol error event.
"""

import json
import logging
import os
import sys
from collections.abc import Callable, Mapping
from typing import IO

from pydantic import ValidationError
from returns.result import Failure, Result

from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.base.infrastructure.logging import configure_logging
from meeting_scribe.meetings.domain.errors import WorkerProtocolError
from meeting_scribe.meetings.domain.worker import (
    PROTOCOL_VERSION,
    ResultEvent,
    WorkerEvent,
    WorkerJob,
)
from meeting_scribe.speakers.infrastructure.pyannote_diarizer import PyannoteBackend
from meeting_scribe.transcription.infrastructure.faster_whisper_transcriber import WhisperBackend

logger = logging.getLogger(__name__)

STAGES = ("transcribe", "diarize")
EXIT_OK = 0
EXIT_ERROR = 1

type Emit = Callable[[WorkerEvent], None]
type StageRunner = Callable[[WorkerJob, Emit], Result[ResultEvent, MeetingScribeError]]
type StageFactory = Callable[[], StageRunner]
"""Builds a stage's adapters (and imports their libraries); called once, after the job is valid."""


def open_event_stream() -> IO[str]:
    """Keep the real stdout for protocol events and point fd 1 at stderr (spec 6.2).

    Call before importing any library: a stray ``print`` or progress bar then lands in the log
    instead of corrupting the event stream.
    """
    sys.stdout.flush()
    events = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    return events


def _write(events: IO[str], payload: Mapping[str, object]) -> None:
    events.write(json.dumps(payload, ensure_ascii=False) + "\n")
    events.flush()


def _error(events: IO[str], error_code: str, message: str) -> int:
    _write(events, {"event": "error", "error_code": error_code, "message": message})
    return EXIT_ERROR


def build_transcribe_stage(backend: WhisperBackend | None = None) -> StageRunner:
    """The transcribe stage and its adapters; ``backend`` is a test seam (default: the real one)."""
    # Adapters are imported here, not at module level: ``main`` calls this factory only after the
    # stage and the job are valid, so a refused invocation never imports them (or numpy).
    from meeting_scribe.meetings.application.transcribe_stage import (  # noqa: PLC0415
        run_transcribe_stage,
    )
    from meeting_scribe.meetings.infrastructure.wav_chunk_inspector import (  # noqa: PLC0415
        WavChunkInspector,
    )
    from meeting_scribe.meetings.infrastructure.wav_mix_reader import WavMixReader  # noqa: PLC0415
    from meeting_scribe.transcription.application.transcribe_audio import (  # noqa: PLC0415
        TranscribeAudio,
    )
    from meeting_scribe.transcription.infrastructure.faster_whisper_backend import (  # noqa: PLC0415
        FasterWhisperBackend,
    )
    from meeting_scribe.transcription.infrastructure.faster_whisper_transcriber import (  # noqa: PLC0415
        FasterWhisperTranscriber,
    )
    from meeting_scribe.transcription.infrastructure.json_words_cache import (  # noqa: PLC0415
        JsonWordsCache,
    )

    transcriber = FasterWhisperTranscriber(backend or FasterWhisperBackend())
    service = TranscribeAudio(transcriber, JsonWordsCache())

    def run(job: WorkerJob, emit: Emit) -> Result[ResultEvent, MeetingScribeError]:
        return run_transcribe_stage(
            job, audio=WavChunkInspector(), mix=WavMixReader(), transcribe=service, emit=emit
        )

    return run


def build_diarize_stage(backend: PyannoteBackend | None = None) -> StageRunner:
    """The diarize stage and its adapters; ``backend`` is a test seam (default: the real one)."""
    from meeting_scribe.meetings.application.diarize_stage import (  # noqa: PLC0415
        run_diarize_stage,
    )
    from meeting_scribe.meetings.infrastructure.wav_channel_reader import (  # noqa: PLC0415
        WavChannelReader,
    )
    from meeting_scribe.meetings.infrastructure.wav_chunk_inspector import (  # noqa: PLC0415
        WavChunkInspector,
    )
    from meeting_scribe.speakers.application.diarize_audio import DiarizeAudio  # noqa: PLC0415
    from meeting_scribe.speakers.infrastructure.json_turns_cache import (  # noqa: PLC0415
        JsonTurnsCache,
    )
    from meeting_scribe.speakers.infrastructure.pyannote_backend import (  # noqa: PLC0415
        RealPyannoteBackend,
    )
    from meeting_scribe.speakers.infrastructure.pyannote_diarizer import (  # noqa: PLC0415
        PyannoteDiarizer,
    )

    service = DiarizeAudio(PyannoteDiarizer(backend or RealPyannoteBackend()), JsonTurnsCache())

    def run(job: WorkerJob, emit: Emit) -> Result[ResultEvent, MeetingScribeError]:
        return run_diarize_stage(
            job, audio=WavChunkInspector(), channels=WavChannelReader(), diarize=service, emit=emit
        )

    return run


def _default_stages() -> dict[str, StageFactory]:
    return {"transcribe": build_transcribe_stage, "diarize": build_diarize_stage}


def _prepare(
    stage: str, stdin: IO[str], factories: Mapping[str, StageFactory]
) -> WorkerJob | tuple[str, str]:
    """The validated job, or the ``(error_code, message)`` that refuses this invocation."""
    if stage not in STAGES:
        return (
            "WORKER_UNKNOWN_STAGE",
            f"Unknown worker stage {stage!r}; expected one of {', '.join(STAGES)}.",
        )
    if stage not in factories:
        return ("WORKER_STAGE_UNAVAILABLE", f"The {stage} stage is not implemented in this build.")
    try:
        job = WorkerJob.model_validate_json(stdin.read())
    except (ValidationError, ValueError) as error:
        return (WorkerProtocolError.error_code, f"Unreadable job spec: {error}")
    if job.protocol != PROTOCOL_VERSION or job.stage != stage:
        return (
            WorkerProtocolError.error_code,
            (
                f"Job is protocol {job.protocol} for stage {job.stage!r}; this worker speaks "
                f"protocol {PROTOCOL_VERSION} and was started for {stage!r}."
            ),
        )
    return job


def main(
    argv: list[str] | None = None,
    *,
    stdin: IO[str] | None = None,
    events: IO[str] | None = None,
    stages: Mapping[str, StageFactory] | None = None,
) -> int:
    """Run one stage; the keyword arguments are test seams (a real worker passes none)."""
    args = sys.argv[1:] if argv is None else argv
    if events is None:
        events = open_event_stream()
        configure_logging("INFO")  # stderr: lands in processing.log, never on the event stream
    stage = args[0] if args else ""
    factories = _default_stages() if stages is None else stages
    prepared = _prepare(stage, stdin or sys.stdin, factories)
    if not isinstance(prepared, WorkerJob):
        return _error(events, *prepared)
    sink = events

    def emit(event: WorkerEvent) -> None:
        sink.write(event.model_dump_json() + "\n")
        sink.flush()

    try:
        result = factories[stage]()(prepared, emit)
    except Exception as error:
        logger.exception("The %s stage crashed.", stage)
        return _error(events, MeetingScribeError.error_code, f"{type(error).__name__}: {error}")
    if isinstance(result, Failure):
        failure = result.failure()
        logger.error("The %s stage failed: %s", stage, failure.error_code)
        return _error(events, failure.error_code, str(failure))
    emit(result.unwrap())
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
