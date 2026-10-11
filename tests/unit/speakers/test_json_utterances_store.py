import json
from pathlib import Path

import pytest
from returns.result import Failure, Success

from meeting_scribe.speakers.domain.turns import Utterance
from meeting_scribe.speakers.infrastructure.json_utterances_store import JsonUtterancesStore


def _write(directory: Path, content: str | bytes) -> None:
    path = directory / "utterances.json"
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def test_load_reads_the_utterances_written_by_processing(tmp_path: Path) -> None:
    document = [
        {"speaker": "S1", "start": 0.5, "end": 2.0, "text": "good morning, mañana"},
        {"speaker": "ME", "start": 3.0, "end": 4.5, "text": "hello"},
    ]
    _write(tmp_path, json.dumps(document, ensure_ascii=False))

    loaded = JsonUtterancesStore().load(tmp_path)

    assert loaded == Success(
        (
            Utterance(speaker="S1", start=0.5, end=2.0, text="good morning, mañana"),
            Utterance(speaker="ME", start=3.0, end=4.5, text="hello"),
        )
    )


def test_load_of_an_empty_meeting_is_an_empty_tuple(tmp_path: Path) -> None:
    _write(tmp_path, "[]")

    assert JsonUtterancesStore().load(tmp_path) == Success(())


def test_a_missing_file_is_entity_not_found_transcript(tmp_path: Path) -> None:
    failure = JsonUtterancesStore().load(tmp_path)

    assert isinstance(failure, Failure)
    assert failure.failure().error_code == "ENTITY_NOT_FOUND_TRANSCRIPT"


@pytest.mark.parametrize(
    "content",
    [
        "",
        '[{"speaker": "S1", "start": 0, ',  # truncated
        b"\xff\xfe not utf-8",
        '{"speaker": "S1"}',  # not a list
        '[{"speaker": "S1", "start": "private words", "end": 1, "text": "x"}]',
        '[{"speaker": "S1", "start": 0}]',  # missing fields
        "[null]",
    ],
)
def test_a_damaged_file_is_transcript_corrupt_and_never_quotes_content(
    tmp_path: Path, content: str | bytes
) -> None:
    _write(tmp_path, content)

    failure = JsonUtterancesStore().load(tmp_path)

    assert isinstance(failure, Failure)
    assert failure.failure().error_code == "TRANSCRIPT_CORRUPT"
    assert "private" not in str(failure.failure())


def test_write_transcript_replaces_the_file_atomically(tmp_path: Path) -> None:
    store = JsonUtterancesStore()
    (tmp_path / "transcript.md").write_text("old\n", encoding="utf-8")

    written = store.write_transcript(tmp_path, "new text, mañana\n")

    assert written == Success("transcript.md")
    assert (tmp_path / "transcript.md").read_text(encoding="utf-8") == "new text, mañana\n"
    assert [p.name for p in tmp_path.iterdir()] == ["transcript.md"]  # no temporary left behind


def test_a_failed_write_keeps_the_previous_transcript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "transcript.md").write_text("old\n", encoding="utf-8")

    def refuse(*_args: object) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", refuse)

    failure = JsonUtterancesStore().write_transcript(tmp_path, "new\n")

    assert isinstance(failure, Failure)
    assert failure.failure().error_code == "TRANSCRIPT_STORAGE_FAILED"
    assert (tmp_path / "transcript.md").read_text(encoding="utf-8") == "old\n"
    assert [p.name for p in tmp_path.iterdir()] == ["transcript.md"]


def test_writing_into_a_missing_folder_is_a_storage_failure(tmp_path: Path) -> None:
    failure = JsonUtterancesStore().write_transcript(tmp_path / "gone", "x\n")

    assert isinstance(failure, Failure)
    assert failure.failure().error_code == "TRANSCRIPT_STORAGE_FAILED"
