"""``snapshot_is_complete``: a half-downloaded model never counts as cached (spec install 6)."""

import os
from pathlib import Path

import pytest

from meeting_scribe.base.infrastructure.hf_cache import (
    remove_incomplete_blobs,
    snapshot_is_complete,
)
from tests.unit.hf_fakes import make_snapshot


def test_a_snapshot_with_every_required_file_is_complete(tmp_path: Path) -> None:
    snapshot = make_snapshot(tmp_path, {"config.json": b"{}", "vocabulary.json": b"[]"})

    assert snapshot_is_complete(snapshot, ["config.json", "vocabulary.*"])


def test_a_missing_required_file_is_incomplete(tmp_path: Path) -> None:
    snapshot = make_snapshot(tmp_path, {"config.json": b"{}"})

    assert not snapshot_is_complete(snapshot, ["config.json", "model.bin"])


def test_a_link_to_a_missing_blob_is_incomplete(tmp_path: Path) -> None:
    snapshot = make_snapshot(tmp_path, {"config.json": b"{}", "model.bin": b"x"})
    next(snapshot.parents[1].glob("blobs/blob1")).unlink()

    assert not snapshot_is_complete(snapshot, ["config.json", "model.bin"])


def test_an_unfinished_download_blob_is_incomplete(tmp_path: Path) -> None:
    snapshot = make_snapshot(tmp_path, {"config.json": b"{}"})
    (snapshot.parents[1] / "blobs" / "abc.incomplete").write_bytes(b"half")

    assert not snapshot_is_complete(snapshot, ["config.json"])


def test_a_snapshot_folder_that_does_not_exist_is_incomplete(tmp_path: Path) -> None:
    assert not snapshot_is_complete(tmp_path / "nope", ["config.json"])


def test_a_required_folder_with_no_files_is_incomplete(tmp_path: Path) -> None:
    snapshot = make_snapshot(tmp_path, {"config.yaml": b"x"})
    (snapshot / "embedding").mkdir()  # a directory is not a file

    assert not snapshot_is_complete(snapshot, ["config.yaml", "embedding"])


def test_a_required_folder_with_its_weights_is_complete(tmp_path: Path) -> None:
    snapshot = make_snapshot(tmp_path, {"config.yaml": b"x", "embedding/pytorch_model.bin": b"x"})

    assert snapshot_is_complete(snapshot, ["config.yaml", "embedding"])


def test_a_required_folder_with_a_dangling_weight_is_incomplete(tmp_path: Path) -> None:
    snapshot = make_snapshot(
        tmp_path, {"config.yaml": b"x", "embedding/pytorch_model.bin": b"x", "plda/plda.npz": b"x"}
    )
    (snapshot.parents[1] / "blobs" / "blob2").unlink()  # the plda weights never finished

    assert not snapshot_is_complete(snapshot, ["config.yaml", "embedding", "plda"])


def test_a_stalled_blob_blocks_even_when_a_sibling_link_is_newer(tmp_path: Path) -> None:
    """The Hub downloads in parallel: a sibling can finish after a blob of this revision stalled."""
    snapshot = make_snapshot(tmp_path, {"config.json": b"{}", "model.bin": b"x"})
    blobs = snapshot.parents[1] / "blobs"
    stalled = blobs / "stalled.incomplete"
    stalled.write_bytes(b"half")
    old = stalled.stat().st_mtime - 3600
    os.utime(stalled, (old, old))  # older than every link: still not complete

    assert not snapshot_is_complete(snapshot, ["config.json", "model.bin"])


def test_remove_incomplete_blobs_deletes_only_the_leftovers_of_that_repo(tmp_path: Path) -> None:
    snapshot = make_snapshot(tmp_path, {"config.json": b"{}"})
    blobs = snapshot.parents[1] / "blobs"
    (blobs / "a.incomplete").write_bytes(b"x")
    (blobs / "b.incomplete").write_bytes(b"x")
    other = make_snapshot(tmp_path, {"config.json": b"{}"}, repo="other--repo")
    (other.parents[1] / "blobs" / "c.incomplete").write_bytes(b"x")

    assert remove_incomplete_blobs(snapshot) == 2

    assert sorted(path.name for path in blobs.iterdir()) == ["blob0"]
    assert snapshot_is_complete(snapshot, ["config.json"])
    assert (other.parents[1] / "blobs" / "c.incomplete").exists()


def test_remove_incomplete_blobs_tolerates_a_missing_folder_or_a_vanishing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert remove_incomplete_blobs(tmp_path / "nope" / "snapshots" / "rev") == 0
    snapshot = make_snapshot(tmp_path, {"config.json": b"{}"})
    (snapshot.parents[1] / "blobs" / "a.incomplete").write_bytes(b"x")

    def vanished(self: Path, *_: object, **__: object) -> None:
        raise FileNotFoundError(self)

    monkeypatch.setattr(Path, "unlink", vanished)

    assert remove_incomplete_blobs(snapshot) == 0  # never an exception


def test_a_path_vanishing_mid_scan_means_not_cached_never_an_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = make_snapshot(tmp_path, {"config.json": b"{}"})

    def vanished(self: Path, *_: object) -> None:
        raise FileNotFoundError(self)

    monkeypatch.setattr(Path, "rglob", vanished)

    assert snapshot_is_complete(snapshot, ["config.json"]) is False
