"""``snapshot_is_complete``: a half-downloaded model never counts as cached (spec install 6)."""

from pathlib import Path

from meeting_scribe.base.infrastructure.hf_cache import snapshot_is_complete


def _repo(root: Path, files: dict[str, bytes]) -> Path:
    repo = root / "models--fake--model"
    snapshot = repo / "snapshots" / "rev1"
    (repo / "blobs").mkdir(parents=True)
    for index, (name, content) in enumerate(files.items()):
        blob = repo / "blobs" / f"blob{index}"
        blob.write_bytes(content)
        target = snapshot / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(blob)
    return snapshot


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
    (snapshot.parents[1] / "blobs" / "abc.incomplete").write_bytes(b"half")

    assert not snapshot_is_complete(snapshot, ["config.json"])


def test_a_snapshot_folder_that_does_not_exist_is_incomplete(tmp_path: Path) -> None:
    assert not snapshot_is_complete(tmp_path / "nope", ["config.json"])
