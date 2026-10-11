"""The Spanish ``.po`` catalog must ship in the built wheel (spec 11)."""

import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CATALOG = "meeting_scribe/summaries/locale/es/LC_MESSAGES/meeting_scribe.po"


def test_the_po_catalog_ships_in_the_built_wheel(tmp_path: Path) -> None:
    subprocess.run(  # noqa: S603
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],  # noqa: S607
        cwd=ROOT,
        check=True,
        capture_output=True,
    )

    wheels = list(tmp_path.glob("meeting_scribe-*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as wheel:
        assert CATALOG in wheel.namelist()
