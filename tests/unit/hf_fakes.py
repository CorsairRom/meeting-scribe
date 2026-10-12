"""One fake Hugging Face hub cache entry for the tests (never the real cache, never the network)."""

from pathlib import Path

REVISION = "0123456789abcdef0123456789abcdef01234567"


def make_snapshot(
    hub: Path,
    files: dict[str, bytes],
    repo: str = "fake--model",
    revision: str = REVISION,
) -> Path:
    """``<hub>/models--<repo>/snapshots/<revision>/<file>`` linked to ``blobs/``; returns the
    snapshot folder. ``refs/main`` names the revision, as the real cache does."""
    base = hub / f"models--{repo}"
    (base / "refs").mkdir(parents=True, exist_ok=True)
    (base / "refs" / "main").write_text(revision, encoding="utf-8")
    (base / "blobs").mkdir(parents=True, exist_ok=True)
    snapshot = base / "snapshots" / revision
    for index, (name, content) in enumerate(files.items()):
        blob = base / "blobs" / f"blob{index}"
        blob.write_bytes(content)
        link = snapshot / name
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(blob)
    return snapshot
