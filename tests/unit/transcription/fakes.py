"""Fakes for the transcription ports: no model, no audio device, no files outside tmp_path."""

import json
from collections.abc import Callable
from pathlib import Path

from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.audio import Samples
from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.transcription.domain.errors import TranscriptionError
from meeting_scribe.transcription.domain.params import Device, TranscriptionParams
from meeting_scribe.transcription.domain.words import TranscriptionResult, Word

PARAMS = TranscriptionParams(
    model="large-v3-turbo",
    language="es",
    compute_type="float16",
    cpu_compute_type="int8",
    beam_size=5,
    vad_filter=True,
    condition_on_previous_text=False,
)
FINGERPRINT = (("chunk_00000.wav", 480_640, 1_922_604), ("chunk_00001.wav", 480_320, 1_921_324))
VERSIONS = {"faster-whisper": "1.2.1", "ctranslate2": "4.8.2"}
WORDS = (Word(start=0.5, end=0.9, text="hola", probability=0.98),)


class FakeTranscriber:
    def __init__(self, result: TranscriptionResult | None = None) -> None:
        self.result = result or TranscriptionResult(detected_language="es", words=WORDS)
        self.versions = dict(VERSIONS)
        self.calls: list[tuple[TranscriptionParams, Device]] = []
        self.progress_at: tuple[float, ...] = ()
        self.error: MeetingScribeError | None = None

    def engine_versions(self) -> dict[str, str]:
        return self.versions

    def transcribe(
        self,
        audio: Samples,  # noqa: ARG002
        params: TranscriptionParams,
        device: Device,
        on_progress: Callable[[float], None],
    ) -> Result[TranscriptionResult, MeetingScribeError]:
        self.calls.append((params, device))
        for seconds in self.progress_at:
            on_progress(seconds)
        return Failure(self.error) if self.error else Success(self.result)


class InMemoryWordsCache:
    def __init__(self) -> None:
        self.document: dict[str, object] = {}
        self.fail_store = False

    def load(self, meeting_dir: Path) -> dict[str, object]:  # noqa: ARG002
        return json.loads(json.dumps(self.document))  # what a file round trip would give

    def store(
        self,
        meeting_dir: Path,  # noqa: ARG002
        document: dict[str, object],
    ) -> Result[None, TranscriptionError]:
        if self.fail_store:
            return Failure(TranscriptionError("disk full"))
        self.document = document
        return Success(None)


class RecordingReporter:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def started(self, device_label: str) -> None:
        self.events.append(("started", device_label))

    def cache_hit(self, artifact: str) -> None:
        self.events.append(("cache_hit", artifact))

    def progress(self, processed_seconds: float, total_seconds: float) -> None:
        self.events.append(("progress", (processed_seconds, total_seconds)))
