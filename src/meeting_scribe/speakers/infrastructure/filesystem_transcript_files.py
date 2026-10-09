"""Writes samples, ``utterances.json`` and ``transcript.md`` into the meeting folder (spec 5).

A run is staged under ``.assembling/`` and ``commit`` publishes it as one set: the previous complete
set or the new complete one is on disk at every moment a crash can leave behind (the next ``begin``
rolls back an interrupted commit). Every staged file is also written to a temporary name and
renamed, so a half file is never listed. Runs in the worker only (numpy).
"""

import contextlib
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
BACKUP_DIR = ".commit.old"
DONE_DIR = ".commit.done"
MANIFEST_FILE = "manifest.json"
_PUBLISHED = (SAMPLES_DIR, UTTERANCES_FILE, TRANSCRIPT_FILE)
CLIP_GAP_SECONDS = 0.4
PEAK_DBFS = -3.0
_PEAK = 10 ** (PEAK_DBFS / 20)


class FilesystemTranscriptFiles:
    def begin(self, meeting_dir: Path) -> Result[None, DiarizationError]:
        try:
            _recover(meeting_dir)  # a commit that was killed half way is undone first
            shutil.rmtree(meeting_dir / STAGING_DIR, ignore_errors=True)
            (meeting_dir / STAGING_DIR).mkdir()  # the meeting folder must exist
            (meeting_dir / STAGING_DIR / SAMPLES_DIR).mkdir()
        except (OSError, ValueError) as error:
            return Failure(DiarizationError(f"Cannot prepare {STAGING_DIR}/: {error}"))
        return Success(None)

    def write_sample(
        self, meeting_dir: Path, speaker_id: str, signal: Samples, clips: Sequence[Clip]
    ) -> Result[str, DiarizationError]:
        relative = f"{SAMPLES_DIR}/{speaker_id}.wav"
        content = _wav_bytes(_joined_pcm(signal, clips))
        return _atomic(meeting_dir / STAGING_DIR / relative, content, relative)

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
        """Publish the staged files as one set: the previous complete set or the new complete one.

        The previous files move into ``.commit.old/`` (with a manifest written first), the staged
        ones move in, and renaming ``.commit.old`` to ``.commit.done`` is the commit point. A
        failure rolls back at once; a crash is rolled back by the next ``begin`` or ``commit``.
        """
        try:
            _recover(meeting_dir)
            _publish(meeting_dir)
        except (OSError, ValueError) as error:
            with contextlib.suppress(OSError, ValueError):  # the next begin retries
                _recover(meeting_dir)
            return Failure(DiarizationError(f"Cannot publish the processing results: {error}"))
        shutil.rmtree(meeting_dir / STAGING_DIR, ignore_errors=True)
        return Success(None)

    def discard(self, meeting_dir: Path) -> None:
        shutil.rmtree(meeting_dir / STAGING_DIR, ignore_errors=True)


def _move(source: Path, target: Path) -> None:
    """The one rename every publish step goes through (a test seam for injected failures)."""
    source.replace(target)


def _publish(meeting_dir: Path) -> None:
    staging, backup = meeting_dir / STAGING_DIR, meeting_dir / BACKUP_DIR
    staged = [name for name in _PUBLISHED if (staging / name).exists()]
    had = [name for name in staged if (meeting_dir / name).exists()]
    backup.mkdir()
    manifest = backup / f".{MANIFEST_FILE}.tmp"
    manifest.write_text(json.dumps({"replace": staged, "had": had}), encoding="utf-8")
    _move(manifest, backup / MANIFEST_FILE)  # from here on a crash is rolled back
    for name in staged:
        if name in had:
            _move(meeting_dir / name, backup / name)
        _move(staging / name, meeting_dir / name)
    _move(backup, meeting_dir / DONE_DIR)  # the commit point
    shutil.rmtree(meeting_dir / DONE_DIR, ignore_errors=True)


def _recover(meeting_dir: Path) -> None:
    """Undo a commit that did not reach its commit point; drop one that did."""
    shutil.rmtree(meeting_dir / DONE_DIR, ignore_errors=True)
    backup = meeting_dir / BACKUP_DIR
    if not backup.exists():
        return
    manifest = backup / MANIFEST_FILE
    if manifest.exists():
        plan = json.loads(manifest.read_text(encoding="utf-8"))
        for name in plan["replace"]:
            if (backup / name).exists():
                _remove(meeting_dir / name)  # the new copy, or nothing
                _move(backup / name, meeting_dir / name)
            elif name not in plan["had"]:
                _remove(meeting_dir / name)  # there was no previous one: the new copy goes
    shutil.rmtree(backup, ignore_errors=True)


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


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
