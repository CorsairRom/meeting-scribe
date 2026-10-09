"""After diarization: "Me", word assignment, utterances, transcript and voice samples (spec 2.4-5).

Everything here is cheap CPU work that re-runs whenever the diarize worker runs and is never
cached, so changing a threshold takes effect on Reprocess without a model run (spec 4).
"""

import logging
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError
from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.audio import Samples
from meeting_scribe.base.domain.errors import InvalidValueError, MeetingScribeError
from meeting_scribe.speakers.domain.assignment import FALLBACK_SPEAKER, assign_speakers
from meeting_scribe.speakers.domain.diarization import DiarizationResult
from meeting_scribe.speakers.domain.me_detection import (
    DiarizedChannel,
    MeDetection,
    detect_me,
    merge_turns,
)
from meeting_scribe.speakers.domain.params import AssemblyParams
from meeting_scribe.speakers.domain.ports import TranscriptFiles, TurnsCache
from meeting_scribe.speakers.domain.samples import (
    clean_turns_of,
    me_turns_with_quiet_system,
    select_sample_clips,
)
from meeting_scribe.speakers.domain.transcript import number_speakers, render_transcript
from meeting_scribe.speakers.domain.turns import ME, UNKNOWN, SpeakerLabel, TimedWord, Turn
from meeting_scribe.speakers.domain.utterances import group_utterances

logger = logging.getLogger(__name__)

_MIC_SPEAKERS = (ME, UNKNOWN)
"""Speakers heard on the microphone only: their voice samples come from channel 0."""


class ExtractionReporter(Protocol):
    def extracting_samples(self) -> None:
        """Diarization and assignment are done; samples and transcript are being written."""
        ...


class WordLike(Protocol):
    """Any transcribed word: the transcription domain's ``Word`` fits (domains do not import)."""

    @property
    def start(self) -> float: ...

    @property
    def end(self) -> float: ...

    @property
    def text(self) -> str: ...


class AssemblyRequest(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    params: AssemblyParams
    words: Sequence[TimedWord]
    diarization: DiarizationResult
    channel: DiarizedChannel
    mic_energies: Sequence[float]
    system_energies: Sequence[float]
    load_channel: Callable[[DiarizedChannel], Result[Samples, MeetingScribeError]]
    """Called only to cut samples: one decode per channel that has a speaker to sample."""

    @classmethod
    def from_spec(
        cls,
        spec: Mapping[str, JsonValue],
        words: Sequence[WordLike],
        diarization: DiarizationResult,
        channel: DiarizedChannel,
        mic_energies: Sequence[float],
        system_energies: Sequence[float],
        load_channel: Callable[[DiarizedChannel], Result[Samples, MeetingScribeError]],
    ) -> Result[Self, InvalidValueError]:
        """Build a request from the job spec's ``speakers`` section; never raises."""
        try:
            return Success(
                cls.model_validate(
                    {
                        "params": dict(spec),
                        "words": [TimedWord(start=w.start, end=w.end, text=w.text) for w in words],
                        "diarization": diarization,
                        "channel": channel,
                        "mic_energies": mic_energies,
                        "system_energies": system_energies,
                        "load_channel": load_channel,
                    }
                )
            )
        except ValidationError as error:
            fields = ", ".join(".".join(str(part) for part in e["loc"]) for e in error.errors())
            return Failure(InvalidValueError(f"Invalid speakers job spec: {fields}."))


class SpeakerSummary(BaseModel):
    """One listed speaker; the stage maps it to the ``meeting.json`` entry."""

    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    is_me: bool
    talk_seconds: float
    utterance_count: int
    sample: str = ""


class AssemblyOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifacts: dict[str, str]
    speakers: tuple[SpeakerSummary, ...]


class AssembleTranscript:
    def __init__(self, cache: TurnsCache, files: TranscriptFiles) -> None:
        self._cache = cache
        self._files = files

    def run(
        self, meeting_dir: Path, request: AssemblyRequest, reporter: ExtractionReporter
    ) -> Result[AssemblyOutcome, MeetingScribeError]:
        params = request.params
        detection = self._detect(request)
        turns = merge_turns(
            request.diarization.exclusive, detection.me_turns, detection.unknown_turns
        )
        words = assign_speakers(request.words, turns)
        utterances = group_utterances(words, params.utterance_gap_seconds)
        labels = number_speakers(utterances)
        self._remember_me(meeting_dir, detection)

        reporter.extracting_samples()
        samples = self._write_samples(meeting_dir, request, detection, labels)
        if isinstance(samples, Failure):
            return samples
        utterances_file = self._files.write_utterances(meeting_dir, utterances, labels)
        if isinstance(utterances_file, Failure):
            return utterances_file
        transcript_file = self._files.write_transcript(
            meeting_dir, render_transcript(utterances, labels)
        )
        if isinstance(transcript_file, Failure):
            return transcript_file

        talk: dict[str, float] = defaultdict(float)
        count: dict[str, int] = defaultdict(int)
        for utterance in utterances:
            talk[utterance.speaker] += utterance.end - utterance.start
            count[utterance.speaker] += 1
        speakers = tuple(
            SpeakerSummary(
                id=label.id,
                label=label.label,
                is_me=label.id == ME,
                talk_seconds=round(talk[speaker], 3),
                utterance_count=count[speaker],
                sample=samples.unwrap().get(speaker, ""),
            )
            for speaker, label in labels.items()
        )
        return Success(
            AssemblyOutcome(
                artifacts={
                    "utterances": utterances_file.unwrap(),
                    "transcript": transcript_file.unwrap(),
                },
                speakers=speakers,
            )
        )

    @staticmethod
    def _detect(request: AssemblyRequest) -> MeDetection:
        """ "Me" exists only where the microphone and the system audio are separate (spec 2.4)."""
        if request.channel == "mic":  # in person: everyone is on the mic, nothing to subtract
            return MeDetection(me_turns=(), unknown_turns=(), bleed=0.0)
        return detect_me(
            request.mic_energies,
            request.system_energies,
            request.diarization.exclusive,
            request.params.me_params(),
        )

    def _remember_me(self, meeting_dir: Path, detection: MeDetection) -> None:
        """Add ``k`` and the "Me"/"Unknown" turns to ``turns.json`` (spec 4); never fatal."""
        document = self._cache.load(meeting_dir)
        data = document.get("data")
        if not isinstance(data, dict):
            return
        data.update(
            bleed=detection.bleed,
            me_turns=[turn.model_dump() for turn in detection.me_turns],
            unknown_turns=[turn.model_dump() for turn in detection.unknown_turns],
        )
        stored = self._cache.store(meeting_dir, document)
        if isinstance(stored, Failure):
            logger.warning("Could not add the Me turns to turns.json: %s", stored.failure())

    def _write_samples(
        self,
        meeting_dir: Path,
        request: AssemblyRequest,
        detection: MeDetection,
        labels: Mapping[str, SpeakerLabel],
    ) -> Result[dict[str, str], MeetingScribeError]:
        """``speaker key -> relative sample path`` for every listed speaker with a clean turn."""
        reset = self._files.reset_samples(meeting_dir)
        if isinstance(reset, Failure):
            return reset
        params = request.params.sample_params()
        diarized = [
            speaker for speaker in labels if speaker not in (*_MIC_SPEAKERS, FALLBACK_SPEAKER)
        ]
        wanted: dict[str, tuple[DiarizedChannel, tuple[Turn, ...]]] = {}
        for speaker in diarized:
            wanted[speaker] = (
                request.channel,
                clean_turns_of(speaker, request.diarization.turns),
            )
        mic_turns = {
            ME: detection.me_turns,
            UNKNOWN: detection.unknown_turns,
        }
        for speaker, turns in mic_turns.items():
            if speaker in labels:
                quiet = me_turns_with_quiet_system(
                    turns, request.system_energies, request.params.me_noise_margin_db
                )
                wanted[speaker] = ("mic", quiet)
        paths: dict[str, str] = {}
        for channel in ("system", "mic"):
            chosen = {
                speaker: select_sample_clips(turns, params)
                for speaker, (wanted_channel, turns) in wanted.items()
                if wanted_channel == channel
            }
            chosen = {speaker: clips for speaker, clips in chosen.items() if clips}
            if not chosen:
                continue
            signal = request.load_channel(channel)
            if isinstance(signal, Failure):
                return signal
            for speaker, clips in chosen.items():
                written = self._files.write_sample(
                    meeting_dir, labels[speaker].id, signal.unwrap(), clips
                )
                if isinstance(written, Failure):
                    return written
                paths[speaker] = written.unwrap()
        return Success(paths)
