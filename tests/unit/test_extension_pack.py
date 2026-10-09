import shutil
import subprocess
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXTENSION_DIR = ROOT / "gnome-extension" / "meeting-scribe@meetingscribe.dev"
ZIP_NAME = "meeting-scribe@meetingscribe.dev.shell-extension.zip"

pytestmark = pytest.mark.skipif(
    shutil.which("gnome-extensions") is None, reason="gnome-extensions CLI not installed"
)


@pytest.fixture(name="bundle")
def _bundle(tmp_path: Path) -> Iterator[zipfile.ZipFile]:
    subprocess.run(  # noqa: S603  # fixed argv, no untrusted input
        ["make", "--no-print-directory", "extension-pack", f"PACK_DIR={tmp_path}"],  # noqa: S607
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    with zipfile.ZipFile(tmp_path / ZIP_NAME) as bundle:
        yield bundle


def test_bundle_contains_exactly_the_extension_files(bundle: zipfile.ZipFile) -> None:
    expected = {
        path.relative_to(EXTENSION_DIR).as_posix()
        for path in EXTENSION_DIR.rglob("*")
        if path.is_file()
    }
    packed = {name for name in bundle.namelist() if not name.endswith("/")}
    assert packed == expected
    top_level = {"metadata.json", "extension.js", "stylesheet.css"}
    assert top_level <= packed
    assert all(name.startswith("lib/") for name in packed - top_level)


def test_bundle_has_no_tests_or_dev_files(bundle: zipfile.ZipFile) -> None:
    for name in bundle.namelist():
        assert "test" not in name
        assert not name.startswith((".", "dist", "gnome-extension"))


def test_bundled_metadata_matches_the_source(bundle: zipfile.ZipFile) -> None:
    assert bundle.read("metadata.json") == (EXTENSION_DIR / "metadata.json").read_bytes()
