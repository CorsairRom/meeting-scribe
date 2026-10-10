import json
import wave
from pathlib import Path

import pytest
from returns.result import Failure

np = pytest.importorskip("numpy")

from meeting_scribe.meetings.domain.errors import MeetingStorageError
from meeting_scribe.speakers.application.assemble_transcript import (
    AssembleTranscript,
    AssemblyRequest,
)
from meeting_scribe.speakers.domain.diarization import DiarizationResult
from meeting_scribe.speakers.domain.samples import Clip
from meeting_scribe.speakers.domain.turns import SpeakerLabel, TimedWord, Utterance
from meeting_scribe.speakers.infrastructure import filesystem_transcript_files as module
from meeting_scribe.speakers.infrastructure.filesystem_transcript_files import (
    FilesystemTranscriptFiles,
)
from tests.unit.speakers.envelopes import SPEECH, envelope
from tests.unit.speakers.fakes import SPEAKERS_SPEC, InMemoryTurnsCache

RATE = 16_000


def _tone(seconds: float, amplitude: float) -> "np.ndarray":
    t = np.arange(round(seconds * RATE)) / RATE
    return (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def test_a_voice_sample_is_mono_16_bit_with_gaps_and_a_minus_3_dbfs_peak(tmp_path: Path) -> None:
    files = FilesystemTranscriptFiles()
    files.begin(tmp_path).unwrap()
    signal = np.concatenate([_tone(6, 0.1), _tone(4, 0.1), _tone(2, 0.1)])
    clips = [Clip(start=0, end=6), Clip(start=6, end=10), Clip(start=10, end=12)]

    path = files.write_sample(tmp_path, "S1", signal, clips).unwrap()
    files.commit(tmp_path).unwrap()

    assert path == "samples/S1.wav"
    with wave.open(str(tmp_path / path)) as reader:
        assert (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) == (1, 2, RATE)
        assert reader.getnframes() / RATE == pytest.approx(12.8)  # 6 + 4 + 2 + 2 x 0.4 s
        pcm = np.frombuffer(reader.readframes(reader.getnframes()), dtype="<i2")
    assert pcm.max() / 32767 == pytest.approx(10 ** (-3 / 20), abs=0.002)
    gap = pcm[6 * RATE : 6 * RATE + round(0.4 * RATE)]
    assert not gap.any()  # silence between clips


def test_a_silent_signal_is_written_without_dividing_by_zero(tmp_path: Path) -> None:
    files = FilesystemTranscriptFiles()
    files.begin(tmp_path).unwrap()

    files.write_sample(tmp_path, "ME", np.zeros(RATE * 3, dtype=np.float32), [Clip(start=0, end=2)])
    files.commit(tmp_path).unwrap()

    with wave.open(str(tmp_path / "samples" / "ME.wav")) as reader:
        assert not any(reader.readframes(reader.getnframes()))


def _previous_run(meeting: Path) -> None:
    (meeting / "samples").mkdir()
    (meeting / "samples" / "S9.wav").write_bytes(b"old sample")
    (meeting / "utterances.json").write_text("old utterances", encoding="utf-8")
    (meeting / "transcript.md").write_text("old transcript", encoding="utf-8")


def _snapshot(meeting: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(meeting)): path.read_bytes()
        for path in sorted(meeting.rglob("*"))
        if path.is_file()
    }


def test_nothing_of_the_previous_run_changes_until_commit(tmp_path: Path) -> None:
    files = FilesystemTranscriptFiles()
    _previous_run(tmp_path)
    before = _snapshot(tmp_path)

    files.begin(tmp_path).unwrap()
    files.write_sample(tmp_path, "S1", _tone(3, 0.1), [Clip(start=0, end=2)]).unwrap()
    labels = {"SPEAKER_07": SpeakerLabel(id="S1", label="Speaker 1")}
    files.write_utterances(
        tmp_path, [Utterance(speaker="SPEAKER_07", start=0, end=1, text="nuevo")], labels
    ).unwrap()
    files.write_transcript(tmp_path, "new transcript").unwrap()

    visible = {
        name: data for name, data in _snapshot(tmp_path).items() if ".assembling" not in name
    }
    assert visible == before


def test_commit_replaces_samples_utterances_and_transcript_together(tmp_path: Path) -> None:
    files = FilesystemTranscriptFiles()
    _previous_run(tmp_path)
    labels = {"SPEAKER_07": SpeakerLabel(id="S1", label="Speaker 1")}

    files.begin(tmp_path).unwrap()
    files.write_sample(tmp_path, "S1", _tone(3, 0.1), [Clip(start=0, end=2)]).unwrap()
    files.write_utterances(
        tmp_path, [Utterance(speaker="SPEAKER_07", start=0, end=1, text="nuevo")], labels
    ).unwrap()
    files.write_transcript(tmp_path, "new transcript").unwrap()
    files.commit(tmp_path).unwrap()

    assert sorted(path.name for path in (tmp_path / "samples").iterdir()) == ["S1.wav"]  # S9 gone
    assert (tmp_path / "transcript.md").read_text(encoding="utf-8") == "new transcript"
    assert "nuevo" in (tmp_path / "utterances.json").read_text(encoding="utf-8")
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "samples",
        "transcript.md",
        "utterances.json",
    ]  # no staging or backup left behind


def test_discard_drops_the_staged_files_and_keeps_the_previous_run(tmp_path: Path) -> None:
    files = FilesystemTranscriptFiles()
    _previous_run(tmp_path)
    before = _snapshot(tmp_path)

    files.begin(tmp_path).unwrap()
    files.write_transcript(tmp_path, "half").unwrap()
    files.discard(tmp_path)

    assert _snapshot(tmp_path) == before
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "samples",
        "transcript.md",
        "utterances.json",
    ]


def test_begin_clears_staging_left_by_a_crashed_run(tmp_path: Path) -> None:
    files = FilesystemTranscriptFiles()
    files.begin(tmp_path).unwrap()
    files.write_transcript(tmp_path, "from the crashed run").unwrap()

    files.begin(tmp_path).unwrap()
    files.commit(tmp_path).unwrap()

    assert not (tmp_path / "transcript.md").exists()  # the crashed run's file was not adopted


def test_a_failed_job_keeps_the_last_good_outputs(tmp_path: Path) -> None:
    """The whole use case, real files: a late failure must not cost the previous samples."""
    _previous_run(tmp_path)
    before = _snapshot(tmp_path)
    cache = InMemoryTurnsCache()
    envelope_ = envelope(60, [(10, 20, SPEECH)])
    request = AssemblyRequest.from_spec(
        SPEAKERS_SPEC,
        [TimedWord(start=12, end=12.5, text="hola")],
        DiarizationResult(turns=(), exclusive=()),
        "system",
        envelope_,
        envelope(60),
        lambda _: Failure(MeetingStorageError("unreadable chunk")),
    ).unwrap()

    result = AssembleTranscript(cache, FilesystemTranscriptFiles()).run(
        tmp_path, request, _NoReporter()
    )

    assert isinstance(result, Failure)
    assert _snapshot(tmp_path) == before


class _NoReporter:
    def extracting_samples(self) -> None:
        return None


def test_utterances_and_transcript_are_written_utf_8_atomically(tmp_path: Path) -> None:
    files = FilesystemTranscriptFiles()
    labels = {"SPEAKER_07": SpeakerLabel(id="S1", label="Speaker 1")}
    utterances = [Utterance(speaker="SPEAKER_07", start=1.0, end=2.0, text="año nuevo")]
    files.begin(tmp_path).unwrap()

    assert files.write_utterances(tmp_path, utterances, labels).unwrap() == "utterances.json"
    assert files.write_transcript(tmp_path, "[00:00:01] **Speaker 1:** año nuevo\n").unwrap() == (
        "transcript.md"
    )
    files.commit(tmp_path).unwrap()

    document = json.loads((tmp_path / "utterances.json").read_text(encoding="utf-8"))
    assert document == [{"speaker": "S1", "start": 1.0, "end": 2.0, "text": "año nuevo"}]
    assert "año" in (tmp_path / "transcript.md").read_text(encoding="utf-8")
    assert not list(tmp_path.rglob(".*.tmp"))


def test_a_write_failure_is_a_diarization_error(tmp_path: Path) -> None:
    files = FilesystemTranscriptFiles()

    assert files.write_transcript(tmp_path / "missing", "x").failure().error_code == (
        "DIARIZATION_FAILED"
    )
    assert files.begin(tmp_path / "missing").failure().error_code == "DIARIZATION_FAILED"
    assert files.commit(tmp_path / "missing").failure().error_code == "DIARIZATION_FAILED"


# -- the published set is all-or-nothing (f34a: R1/R2/R3/R4 commit findings) --------------------

_NAMES = ("samples/S1.wav", "utterances.json", "transcript.md")


class _Crash(BaseException):
    """A process kill: nothing in the commit may catch it, so no rollback code runs."""


def _run(files: FilesystemTranscriptFiles, folder: Path, tag: str) -> None:
    files.begin(folder).unwrap()
    signal = _tone(1, 0.1 if tag == "old" else 0.05)
    files.write_sample(folder, "S1", signal, [Clip(start=0, end=1)])
    document = [{"speaker": "S1", "start": 0.0, "end": 1.0, "text": tag}]
    (folder / ".assembling" / "utterances.json").write_text(json.dumps(document))
    (folder / ".assembling" / "transcript.md").write_text(tag)


def _published(folder: Path) -> dict[str, str | None]:
    return {
        name: (folder / name).read_bytes().hex()[:64] if (folder / name).exists() else None
        for name in _NAMES
    }


def _fail_nth_move(monkeypatch: pytest.MonkeyPatch, nth: int, error: BaseException) -> None:
    real, calls = module._move, {"count": 0}

    def move(source: Path, target: Path) -> None:
        calls["count"] += 1
        if calls["count"] == nth:
            raise error
        real(source, target)

    monkeypatch.setattr(module, "_move", move)


# With a previous set a commit makes 8 moves (manifest, two per published name, the commit-point
# rename); without one it makes 5. The last step of each is the `.commit.old` -> `.commit.done`
# rename, so a failure or a crash exactly at the commit point is covered too.
_STEPS = [(True, n) for n in range(1, 9)] + [(False, n) for n in range(1, 6)]


@pytest.mark.parametrize(("had_previous", "step"), _STEPS)
def test_a_failure_at_any_step_keeps_the_previous_set_or_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, had_previous: bool, step: int
) -> None:
    files = FilesystemTranscriptFiles()
    before: dict[str, str | None] = dict.fromkeys(_NAMES)
    if had_previous:
        _run(files, tmp_path, "old")
        files.commit(tmp_path).unwrap()
        before = _published(tmp_path)
    _run(files, tmp_path, "new")
    _fail_nth_move(monkeypatch, step, OSError("disk says no"))

    result = files.commit(tmp_path)
    files.discard(tmp_path)

    assert isinstance(result, Failure)
    assert _published(tmp_path) == before
    assert not [p.name for p in tmp_path.iterdir() if p.name.startswith(".commit")]


@pytest.mark.parametrize(("had_previous", "step"), _STEPS)
def test_a_crash_at_any_step_is_repaired_by_the_next_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, had_previous: bool, step: int
) -> None:
    files = FilesystemTranscriptFiles()
    before: dict[str, str | None] = dict.fromkeys(_NAMES)
    if had_previous:
        _run(files, tmp_path, "old")
        files.commit(tmp_path).unwrap()
        before = _published(tmp_path)
    _run(files, tmp_path, "new")
    real = module._move
    _fail_nth_move(monkeypatch, step, _Crash())

    with pytest.raises(_Crash):
        files.commit(tmp_path)
    monkeypatch.setattr(module, "_move", real)
    files.begin(tmp_path).unwrap()  # the next run starts: it restores the previous set

    assert _published(tmp_path) == before  # including samples (R4 crash window)
    assert not [p.name for p in tmp_path.iterdir() if p.name.startswith(".commit")]


def test_a_commit_that_finished_but_was_not_cleaned_up_keeps_the_new_set(tmp_path: Path) -> None:
    files = FilesystemTranscriptFiles()
    _run(files, tmp_path, "old")
    files.commit(tmp_path).unwrap()
    _run(files, tmp_path, "new")
    files.commit(tmp_path).unwrap()
    published = _published(tmp_path)
    (tmp_path / ".commit.done").mkdir()  # a kill right after the commit point
    (tmp_path / ".commit.done" / "utterances.json").write_text("stale")

    files.begin(tmp_path).unwrap()

    assert _published(tmp_path) == published
    assert not (tmp_path / ".commit.done").exists()


@pytest.mark.parametrize(("had_previous", "moves"), [(True, 8), (False, 5)])
def test_the_step_matrix_ends_at_the_commit_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, had_previous: bool, moves: int
) -> None:
    files = FilesystemTranscriptFiles()
    if had_previous:
        _run(files, tmp_path, "old")
        files.commit(tmp_path).unwrap()
    _run(files, tmp_path, "new")
    real, targets = module._move, []

    def spy(source: Path, target: Path) -> None:
        targets.append(target.name)
        real(source, target)

    monkeypatch.setattr(module, "_move", spy)
    files.commit(tmp_path).unwrap()

    assert len(targets) == moves
    assert targets[-1] == ".commit.done"
