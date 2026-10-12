"""``snapshot_is_complete``: a half-downloaded model never counts as cached (spec install 6)."""

import os
from pathlib import Path

from meeting_scribe.base.infrastructure.hf_cache import snapshot_is_complete
from tests.unit.hf_fakes import make_snapshot


def _repo(root: Path, files: dict[str, bytes]) -> Path:
    return make_snapshot(root, files)


def test_a_snapshot_with_every_required_file_is_complete(tmp_path: Path) -> None:
    snapshot = _repo(tmp_path, {"config.json": b"{}", "vocabulary.json": b"[]"})

    assert snapshot_is_complete(snapshot, ["config.json", "vocabulary.*"])


def test_a_missing_required_file_is_incomplete(tmp_path: Path) -> None:
    snapshot = _repo(tmp_path, {"config.json": b"{}"})

    assert not snapshot_is_complete(snapshot, ["config.json", "model.bin"])


def test_a_link_to_a_missing_blob_is_incomplete(tmp_path: Path) -> None:
    snapshot = _repo(tmp_path, {"config.json": b"{}", "model.bin": b"x"})
    next(snapshot.parents[1].glob("blobs/blob1")).unlink()

    assert not snapshot_is_complete(snapshot, ["config.json", "model.bin"])


def test_an_unfinished_download_blob_is_incomplete(tmp_path: Path) -> None:
    snapshot = _repo(tmp_path, {"config.json": b"{}"})
    link = snapshot / "config.json"
    stamp = link.lstat().st_mtime - 60
    os.utime(link, (stamp, stamp), follow_symlinks=False)
    (snapshot.parents[1] / "blobs" / "abc.incomplete").write_bytes(b"half")

    assert not snapshot_is_complete(snapshot, ["config.json"])


def test_a_snapshot_folder_that_does_not_exist_is_incomplete(tmp_path: Path) -> None:
    assert not snapshot_is_complete(tmp_path / "nope", ["config.json"])


def test_a_required_folder_with_no_files_is_incomplete(tmp_path: Path) -> None:
    snapshot = _repo(tmp_path, {"config.yaml": b"x"})
    (snapshot / "embedding").mkdir()  # a directory is not a file

    assert not snapshot_is_complete(snapshot, ["config.yaml", "embedding"])


def test_a_required_folder_holding_only_a_stray_file_is_incomplete(tmp_path: Path) -> None:
    snapshot = _repo(tmp_path, {"config.yaml": b"x", "embedding/notes.txt": b"x"})

    assert not snapshot_is_complete(snapshot, ["config.yaml", "embedding"])


def test_a_required_folder_with_its_weights_is_complete(tmp_path: Path) -> None:
    snapshot = _repo(tmp_path, {"config.yaml": b"x", "embedding/pytorch_model.bin": b"x"})

    assert snapshot_is_complete(snapshot, ["config.yaml", "embedding"])


def test_a_required_folder_with_a_dangling_weight_is_incomplete(tmp_path: Path) -> None:
    snapshot = _repo(
        tmp_path, {"config.yaml": b"x", "embedding/pytorch_model.bin": b"x", "plda/plda.npz": b"x"}
    )
    (snapshot.parents[1] / "blobs" / "blob2").unlink()  # the plda weights never finished

    assert not snapshot_is_complete(snapshot, ["config.yaml", "embedding", "plda"])


def _age(path: Path, seconds: float) -> None:
    stamp = path.stat().st_mtime - seconds
    os.utime(path, (stamp, stamp))


def test_a_stale_incomplete_blob_older_than_the_snapshot_does_not_block(tmp_path: Path) -> None:
    snapshot = _repo(tmp_path, {"config.json": b"{}"})
    stale = snapshot.parents[1] / "blobs" / "old.incomplete"  # a leftover of an older revision
    stale.write_bytes(b"half")
    _age(stale, 3600)

    assert snapshot_is_complete(snapshot, ["config.json"])


def test_an_incomplete_blob_newer_than_the_snapshot_files_blocks(tmp_path: Path) -> None:
    snapshot = _repo(tmp_path, {"config.json": b"{}"})
    link = snapshot / "config.json"
    stamp = link.lstat().st_mtime - 3600
    os.utime(link, (stamp, stamp), follow_symlinks=False)  # the files were linked an hour ago
    (snapshot.parents[1] / "blobs" / "new.incomplete").write_bytes(b"half")  # written just now

    assert not snapshot_is_complete(snapshot, ["config.json"])
