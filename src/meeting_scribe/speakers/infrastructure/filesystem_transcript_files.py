"""Writes samples, ``utterances.json`` and ``transcript.md`` into the meeting folder (spec 5).

Every file is written to a temporary name and renamed, so a crash never leaves a half file that
the service would list as a result. Runs in the worker only (numpy).
"""

import io
import json
import os
import shutil
import wave
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.audio import SAMPLE_RATE, Samples
from meeting_scribe.speakers.domain.errors import DiarizationError
from meeting_scribe.speakers.domain.samples import Clip
from meeting_scribe.speakers.domain.turns import SpeakerLabel, Utterance

SAMPLES_DIR = "samples"
UTTERANCES_FILE = "utterances.json"
TRANSCRIPT_FILE = "transcript.md"
CLIP_GAP_SECONDS = 0.4
PEAK_DBFS = -3.0
_PEAK = 10 ** (PEAK_DBFS / 20)


class FilesystemTranscriptFiles:
    def reset_samples(self, meeting_dir: Path) -> Result[None, DiarizationError]:
        try:
            shutil.rmtree(meeting_dir / SAMPLES_DIR, ignore_errors=True)
            (meeting_dir / SAMPLES_DIR).mkdir()
        except OSError as error:
            return Failure(DiarizationError(f"Cannot prepare {SAMPLES_DIR}/: {error}"))
        return Success(None)

    def write_sample(
        self, meeting_dir: Path, speaker_id: str, signal: Samples, clips: Sequence[Clip]
    ) -> Result[str, DiarizationError]:
        relative = f"{SAMPLES_DIR}/{speaker_id}.wav"
        return _atomic(meeting_dir / relative, _wav_bytes(_joined_pcm(signal, clips)), relative)

    def write_utterances(
        self,
        meeting_dir: Path,
        utterances: Sequence[Utterance],
        labels: Mapping[str, SpeakerLabel],
    ) -> Result[str, DiarizationError]:
        document = [
            {
                "speaker": labels[u.speaker].id,
                "start": u.start,
                "end": u.end,
                "text": u.text,
            }
            for u in utterances
        ]
        content = json.dumps(document, ensure_ascii=False).encode("utf-8")
        return _atomic(meeting_dir / UTTERANCES_FILE, content, UTTERANCES_FILE)

    def write_transcript(self, meeting_dir: Path, text: str) -> Result[str, DiarizationError]:
        return _atomic(meeting_dir / TRANSCRIPT_FILE, text.encode("utf-8"), TRANSCRIPT_FILE)


def _joined_pcm(signal: Samples, clips: Sequence[Clip]) -> bytes:
    """Clips separated by 0.4 s of silence, peak-normalized to -3 dBFS, as 16-bit PCM."""
    gap = np.zeros(round(CLIP_GAP_SECONDS * SAMPLE_RATE), dtype=np.float32)
    parts: list[np.ndarray] = []
    for index, clip in enumerate(clips):
        if index:
            parts.append(gap)
        parts.append(signal[round(clip.start * SAMPLE_RATE) : round(clip.end * SAMPLE_RATE)])
    joined = np.concatenate(parts) if parts else gap[:0]
    peak = float(np.max(np.abs(joined))) if len(joined) else 0.0
    if peak > 0:
        joined = joined * (_PEAK / peak)
    return (np.clip(joined, -1.0, 1.0) * 32767).astype("<i2").tobytes()


def _wav_bytes(pcm: bytes) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(SAMPLE_RATE)
        writer.writeframes(pcm)
    return buffer.getvalue()


def _atomic(path: Path, content: bytes, name: str) -> Result[str, DiarizationError]:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        return Failure(DiarizationError(f"Cannot write {name}: {error}"))
    return Success(name)
