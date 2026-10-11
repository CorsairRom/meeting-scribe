from pathlib import Path

import pytest

from meeting_scribe.meetings.infrastructure.filesystem_sample_reader import (
    MAX_SAMPLE_BYTES,
    FilesystemSampleReader,
)

reader = FilesystemSampleReader()


def _write(root: Path, relative: str, content: bytes) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_reads_a_sample_of_the_maximum_size(tmp_path: Path) -> None:
    content = b"\x01" * (16_000 * 2 * 15 + 44)  # 15 s of 16 kHz 16-bit mono plus a header (A-4)
    assert len(content) <= MAX_SAMPLE_BYTES
    _write(tmp_path, "samples/S1.wav", content)

    assert reader.read(tmp_path, "samples/S1.wav").unwrap() == content


@pytest.mark.parametrize("relative", ["samples/S9.wav", "samples", "../outside.wav", "/etc/hosts"])
def test_missing_or_escaping_paths_are_not_found(tmp_path: Path, relative: str) -> None:
    _write(tmp_path.parent, "outside.wav", b"x")
    (tmp_path / "samples").mkdir()

    failure = reader.read(tmp_path, relative).failure()

    assert failure.error_code == "ENTITY_NOT_FOUND_SPEAKER_SAMPLE"


def test_an_oversized_file_is_refused(tmp_path: Path) -> None:
    _write(tmp_path, "samples/S1.wav", b"\x00" * (MAX_SAMPLE_BYTES + 1))

    assert reader.read(tmp_path, "samples/S1.wav").failure().error_code == (
        "ENTITY_NOT_FOUND_SPEAKER_SAMPLE"
    )


def test_a_symlink_out_of_the_folder_is_refused(tmp_path: Path) -> None:
    meeting = tmp_path / "m"
    _write(tmp_path, "secret.wav", b"x")
    (meeting / "samples").mkdir(parents=True)
    (meeting / "samples" / "S1.wav").symlink_to(tmp_path / "secret.wav")

    assert reader.read(meeting, "samples/S1.wav").failure().error_code == (
        "ENTITY_NOT_FOUND_SPEAKER_SAMPLE"
    )
