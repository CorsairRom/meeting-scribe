"""Fakes for the speakers ports: no pyannote, no torch, no model, no files outside tmp_path."""

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.audio import Samples
from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.speakers.domain.diarization import Device, DiarizationResult
from meeting_scribe.speakers.domain.errors import DiarizationError
from meeting_scribe.speakers.domain.params import DiarizationParams
from meeting_scribe.speakers.domain.samples import Clip
from meeting_scribe.speakers.domain.turns import SpeakerLabel, Turn, Utterance
from meeting_scribe.speakers.infrastructure.pyannote_diarizer import (
    Hook,
    OutputLike,
    PipelineLike,
)

PARAMS = DiarizationParams(
    pipeline="pyannote/speaker-diarization-community-1", me_noise_margin_db=10.0
)
FINGERPRINT = (("chunk_00000.wav", 480_640, 1_922_604), ("chunk_00001.wav", 480_320, 1_921_324))
VERSIONS = {"pyannote.audio": "4.0.7", "torch": "2.14.1"}
TURNS = (
    Turn(start=0.0, end=4.0, speaker="SPEAKER_00"),
    Turn(start=3.0, end=8.0, speaker="SPEAKER_01"),
)
EXCLUSIVE = (
    Turn(start=0.0, end=3.0, speaker="SPEAKER_00"),
    Turn(start=3.0, end=8.0, speaker="SPEAKER_01"),
)
RESULT = DiarizationResult(turns=TURNS, exclusive=EXCLUSIVE)


class _OneSecond:
    """Stands in for a signal: only its length is ever asked."""

    def __len__(self) -> int:
        return 16_000


AUDIO = cast("Samples", _OneSecond())


@dataclass(frozen=True)
class FakeSegment:
    start: float
    end: float


class FakeAnnotation:
    def __init__(self, turns: Iterable[Turn]) -> None:
        self._turns = list(turns)

    def itertracks(self, *, yield_label: bool) -> Iterable[tuple[FakeSegment, object, str]]:
        assert yield_label
        return [(FakeSegment(t.start, t.end), "track", t.speaker) for t in self._turns]


class FakeOutput:
    def __init__(self, turns: Iterable[Turn], exclusive: Iterable[Turn]) -> None:
        self.speaker_diarization = FakeAnnotation(turns)
        self.exclusive_speaker_diarization = FakeAnnotation(exclusive)


class FakePipeline:
    def __init__(self, backend: "FakeBackend") -> None:
        self._backend = backend

    def __call__(
        self,
        file: Mapping[str, object],
        *,
        num_speakers: int | None,
        min_speakers: int | None,
        max_speakers: int | None,
        hook: Hook,
    ) -> OutputLike:
        self._backend.calls.append((file, num_speakers, min_speakers, max_speakers))
        for step, total, completed in self._backend.steps:
            hook(step, None, file=file, total=total, completed=completed)
        if self._backend.run_error:
            raise self._backend.run_error
        return FakeOutput(self._backend.turns, self._backend.exclusive)


class FakeBackend:
    def __init__(self) -> None:
        self.turns: tuple[Turn, ...] = TURNS
        self.exclusive: tuple[Turn, ...] = EXCLUSIVE
        self.cuda_devices = 1
        self.load_error: Exception | None = None
        self.run_error: Exception | None = None
        self.steps: tuple[tuple[str, int | None, int | None], ...] = ()
        self.versions = dict(VERSIONS)
        self.calls: list[tuple[Mapping[str, object], int | None, int | None, int | None]] = []
        self.events: list[str] = []

    def prepare(self) -> None:
        self.events.append("prepare")

    def cuda_device_count(self) -> int:
        return self.cuda_devices

    def load_pipeline(self, pipeline: str, device: Device) -> PipelineLike:
        self.events.append(f"load:{pipeline}:{device}")
        if self.load_error:
            raise self.load_error
        return FakePipeline(self)

    def make_input(self, audio: Samples) -> Mapping[str, object]:
        return {"waveform": audio, "sample_rate": 16_000}

    def engine_versions(self) -> dict[str, str]:
        return self.versions


class FakeDiarizer:
    def __init__(self, result: DiarizationResult | None = None) -> None:
        self.result = result or RESULT
        self.versions = dict(VERSIONS)
        self.calls: list[tuple[DiarizationParams, Device]] = []
        self.progress_at: tuple[float, ...] = ()
        self.error: MeetingScribeError | None = None

    def engine_versions(self) -> dict[str, str]:
        return self.versions

    def diarize(
        self,
        audio: Samples,
        params: DiarizationParams,
        device: Device,
        on_progress: Callable[[float], None],
    ) -> Result[DiarizationResult, MeetingScribeError]:
        self.calls.append((params, device))
        for fraction in self.progress_at:
            on_progress(fraction)
        return Failure(self.error) if self.error else Success(self.result)


class InMemoryTurnsCache:
    def __init__(self) -> None:
        self.document: dict[str, object] = {}
        self.fail_store = False

    def load(self, meeting_dir: Path) -> dict[str, object]:
        return json.loads(json.dumps(self.document))  # what a file round trip would give

    def store(
        self, meeting_dir: Path, document: dict[str, object]
    ) -> Result[None, DiarizationError]:
        if self.fail_store:
            return Failure(DiarizationError("disk full"))
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


class FakeTranscriptFiles:
    """Records what the extraction step writes, in order, without touching the disk."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.samples: dict[str, tuple[Clip, ...]] = {}
        self.utterances: list[dict[str, object]] = []
        self.transcript = ""
        self.fail_on: str | None = None

    def _fails(self, name: str) -> Failure[DiarizationError] | None:
        self.calls.append(name)
        return Failure(DiarizationError(f"cannot write {name}")) if self.fail_on == name else None

    def reset_samples(self, meeting_dir: Path) -> Result[None, DiarizationError]:
        return self._fails("reset") or Success(None)

    def write_sample(
        self, meeting_dir: Path, speaker_id: str, signal: Samples, clips: Sequence[Clip]
    ) -> Result[str, DiarizationError]:
        self.samples[speaker_id] = tuple(clips)
        return self._fails("sample") or Success(f"samples/{speaker_id}.wav")

    def write_utterances(
        self,
        meeting_dir: Path,
        utterances: Sequence[Utterance],
        labels: Mapping[str, SpeakerLabel],
    ) -> Result[str, DiarizationError]:
        self.utterances = [
            {"speaker": labels[u.speaker].id, "start": u.start, "text": u.text} for u in utterances
        ]
        return self._fails("utterances") or Success("utterances.json")

    def write_transcript(self, meeting_dir: Path, text: str) -> Result[str, DiarizationError]:
        self.transcript = text
        return self._fails("transcript") or Success("transcript.md")
