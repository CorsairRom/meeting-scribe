"""Is a Hugging Face hub snapshot complete? Pure file checks, no ``huggingface_hub`` import.

``snapshot_download(local_files_only=True)`` returns a snapshot folder as soon as one exists, even
when an interrupted download left it half filled. These checks make "cached" mean usable: every
file the model needs is present and readable, nothing links to a missing blob, and no download is
still in flight for the repository. They never read file contents and never touch the network.
"""

from collections.abc import Iterable
from pathlib import Path

INCOMPLETE_SUFFIX = ".incomplete"
"""Blobs a running or interrupted download writes before they are complete."""


def snapshot_is_complete(snapshot: Path, required: Iterable[str]) -> bool:
    """True when every ``required`` glob matches a readable file and nothing is half downloaded."""
    if not snapshot.is_dir():
        return False
    if any(_dangling(path) for path in snapshot.rglob("*")):
        return False
    blobs = snapshot.parents[1] / "blobs"  # <repo>/snapshots/<revision> -> <repo>/blobs
    if blobs.is_dir() and any(blobs.glob(f"*{INCOMPLETE_SUFFIX}")):
        return False
    return all(any(match.exists() for match in snapshot.glob(pattern)) for pattern in required)


def _dangling(path: Path) -> bool:
    return path.is_symlink() and not path.exists()
