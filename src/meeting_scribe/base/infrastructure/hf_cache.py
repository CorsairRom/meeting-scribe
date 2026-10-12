"""Is a Hugging Face hub snapshot complete? Pure file checks, no ``huggingface_hub`` import.

``snapshot_download(local_files_only=True)`` returns a snapshot folder as soon as one exists, even
when an interrupted download left it half filled. These checks make "cached" mean usable: every
required file is present (a link that resolves; contents are never read), every required folder
holds at least one weight file, nothing links to a missing blob, and no download of this snapshot
was left unfinished. They never touch the network.

Limit: without the repository's file list a file that never started downloading cannot be told
from a file the repository does not have, so a required folder is checked by what it must hold
(a weight file), not file by file.
"""

from collections.abc import Iterable
from pathlib import Path

INCOMPLETE_SUFFIX = ".incomplete"
"""Blobs a running or interrupted download writes before they are complete."""

WEIGHT_SUFFIXES = frozenset({".bin", ".safetensors", ".npz", ".pt", ".pth", ".ckpt", ".onnx"})


def snapshot_is_complete(snapshot: Path, required: Iterable[str]) -> bool:
    """True when every ``required`` glob matches a file (or a folder with a weight file) and
    nothing of this snapshot is half downloaded."""
    if not snapshot.is_dir():
        return False
    entries = list(snapshot.rglob("*"))
    if any(_dangling(path) for path in entries):
        return False
    if _unfinished_download(snapshot, entries):
        return False
    return all(any(_present(match) for match in snapshot.glob(pattern)) for pattern in required)


def _present(path: Path) -> bool:
    if path.is_file():
        return True
    return path.is_dir() and any(
        item.is_file() and item.suffix in WEIGHT_SUFFIXES for item in path.rglob("*")
    )


def _unfinished_download(snapshot: Path, entries: list[Path]) -> bool:
    """An ``*.incomplete`` blob counts only when it is newer than every file this snapshot links.

    A completed download leaves no incomplete blob of its own; a leftover of an abandoned older
    revision is older than the links of the current snapshot, so it must not block ``READY``.
    """
    blobs = snapshot.parents[1] / "blobs"  # <repo>/snapshots/<revision> -> <repo>/blobs
    if not blobs.is_dir():
        return False
    newest = max((path.lstat().st_mtime for path in entries if not path.is_dir()), default=0.0)
    return any(blob.stat().st_mtime >= newest for blob in blobs.glob(f"*{INCOMPLETE_SUFFIX}"))


def _dangling(path: Path) -> bool:
    return path.is_symlink() and not path.exists()
