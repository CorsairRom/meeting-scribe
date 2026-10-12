"""Is a Hugging Face hub snapshot complete? Pure file checks, no ``huggingface_hub`` import.

``snapshot_download(local_files_only=True)`` returns a snapshot folder as soon as one exists, even
when an interrupted download left it half filled. These checks make "cached" mean usable: every
required file is present (a link that resolves; contents are never read), every required folder
holds at least one file, nothing links to a missing blob, and no ``*.incomplete`` blob exists in
the repository. They never touch the network, and a path that vanishes mid-scan means "not
cached", never an exception.

Why any ``*.incomplete`` blocks: the Hub downloads files in parallel, so no timestamp can tell a
stalled blob of the current revision from a leftover of an old one. Leftovers are removed where it
is safe to know they are orphans: after a download run that finished successfully, which has
completed every file of the current revision (``remove_incomplete_blobs``).

Limit: without the repository's file list a file that never started downloading cannot be told
from a file the repository does not have. A required folder is therefore only checked to be
non-empty (the pipeline config names folders, not files); no list of weight formats is kept, so an
unusual format can never leave a complete cache reported as missing.
"""

from collections.abc import Iterable
from pathlib import Path

INCOMPLETE_SUFFIX = ".incomplete"
"""Blobs a running or interrupted download writes before they are complete."""


def snapshot_is_complete(snapshot: Path, required: Iterable[str]) -> bool:
    """True when every ``required`` glob matches a file (or a non-empty folder) and nothing in
    the repository is half downloaded."""
    try:
        return _complete(snapshot, list(required))
    except OSError:  # FileNotFoundError and friends: a path vanished while scanning
        return False


def remove_incomplete_blobs(snapshot: Path) -> int:
    """Delete the ``*.incomplete`` blobs of the snapshot's repository; the number removed.

    Only call it after a download run that exited successfully: every file of the current
    revision is complete then, so what is left is an orphan of an older attempt. Best effort: a
    missing folder or a file that vanishes is not an error.
    """
    removed = 0
    try:
        leftovers = list((snapshot.parents[1] / "blobs").glob(f"*{INCOMPLETE_SUFFIX}"))
    except (OSError, IndexError):
        return 0
    for blob in leftovers:
        try:
            blob.unlink()
        except OSError:
            continue
        removed += 1
    return removed


def _complete(snapshot: Path, required: list[str]) -> bool:
    if not snapshot.is_dir():
        return False
    if any(_dangling(path) for path in snapshot.rglob("*")):
        return False
    blobs = snapshot.parents[1] / "blobs"  # <repo>/snapshots/<revision> -> <repo>/blobs
    if blobs.is_dir() and any(blobs.glob(f"*{INCOMPLETE_SUFFIX}")):
        return False
    return all(any(_present(match) for match in snapshot.glob(pattern)) for pattern in required)


def _present(path: Path) -> bool:
    if path.is_file():
        return True
    return path.is_dir() and any(item.is_file() for item in path.rglob("*"))


def _dangling(path: Path) -> bool:
    return path.is_symlink() and not path.exists()
