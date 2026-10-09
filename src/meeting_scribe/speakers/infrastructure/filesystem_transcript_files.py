"""Writes samples, ``utterances.json`` and ``transcript.md`` into the meeting folder (spec 5).

A run is staged under ``.assembling/`` and ``commit`` swaps it in, so a failure (or a crash) before
the commit leaves the previous run's samples, utterances and transcript untouched. Every staged file
is also written to a temporary name and renamed, so a half file is never listed. Runs in the worker
only (numpy).
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
STAGING_DIR = ".assembling"
OLD_SAMPLES_DIR = ".samples.old"
CLIP_GAP_SECONDS = 0.4
PEAK_DBFS = -3.0
_PEAK = 10 ** (PEAK_DBFS / 20)


class FilesystemTranscriptFiles:
    def begin(self, meeting_dir: Path) -> Result[None, DiarizationError]:
        try:
            shutil.rmtree(meeting_dir / STAGING_DIR, ignore_errors=True)
            (meeting_dir / STAGING_DIR).mkdir()  # the meeting folder must exist
            (meeting_dir / STAGING_DIR / SAMPLES_DIR).mkdir()
        except OSError as error:
            return Failure(DiarizationError(f"Cannot prepare {STAGING_DIR}/: {error}"))
        return Success(None)

    def write_sample(
        self, meeting_dir: Path, speaker_id: str, signal: Samples, clips: Sequence[Clip]
    ) -> Result[str, DiarizationError]:
        relative = f"{SAMPLES_DIR}/{speaker_id}.wav"
        content = _wav_bytes(_joined_pcm(signal, clips))
        return _atomic(meeting_dir / STAGING_DIR / relative, content, relative, relative)

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
        return _atomic(meeting_dir / STAGING_DIR / UTTERANCES_FILE, content, UTTERANCES_FILE)

    def write_transcript(self, meeting_dir: Path, text: str) -> Result[str, DiarizationError]:
        return _atomic(
            meeting_dir / STAGING_DIR / TRANSCRIPT_FILE, text.encode("utf-8"), TRANSCRIPT_FILE
        )

    def commit(self, meeting_dir: Path) -> Result[None, DiarizationError]:
        staging, samples = meeting_dir / STAGING_DIR, meeting_dir / SAMPLES_DIR
        old = meeting_dir / OLD_SAMPLES_DIR
        try:
            shutil.rmtree(old, ignore_errors=True)
            had_samples = samples.exists()
            if had_samples:
                samples.rename(old)
            try:
                (staging / SAMPLES_DIR).rename(samples)
            except OSError:
                if had_samples:
                    old.rename(samples)  # put the previous samples back
                raise
            for name in (UTTERANCES_FILE, TRANSCRIPT_FILE):
                if (staging / name).exists():
                    (staging / name).replace(meeting_dir / name)
        except OSError as error:
            return Failure(DiarizationError(f"Cannot publish the processing results: {error}"))
        shutil.rmtree(old, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)
        return Success(None)

    def discard(self, meeting_dir: Path) -> None:
        shutil.rmtree(meeting_dir / STAGING_DIR, ignore_errors=True)


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


def _atomic(
    path: Path, content: bytes, name: str, result: str | None = None
) -> Result[str, DiarizationError]:
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
    return Success(result or name)
