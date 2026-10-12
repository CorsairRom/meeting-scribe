"""The README prerequisites table and the preflight check ids must stay the same set (AC-14)."""

import json
import re

from tests.unit.install_sandbox import INSTALL, ROOT, Env, sandbox_fixture  # noqa: F401

_ROW = re.compile(r"^\|\s*`([a-z0-9.-]+)`\s*\|")


def _section() -> str:
    """The text of the README "Prerequisites" section, up to the next section."""
    return (ROOT / "README.md").read_text().split("\n## Prerequisites", 1)[1].split("\n## ", 1)[0]


def _readme_ids() -> list[str]:
    return [m.group(1) for line in _section().splitlines() if (m := _ROW.match(line))]


def test_readme_and_preflight_list_the_same_prerequisites(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, "--check", "--json")
    emitted = {check["id"] for check in json.loads(result.stdout)["checks"]}
    listed = _readme_ids()
    assert len(listed) == len(set(listed)), "a check id appears twice in the README table"
    assert set(listed) - emitted == set(), "README rows without a preflight check id"
    assert emitted - set(listed) == set(), "preflight check ids without a README row"


def test_every_readme_row_says_what_is_needed_and_how_to_install_it() -> None:
    rows = [line for line in _section().splitlines() if _ROW.match(line)]
    assert rows
    for row in rows:
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        assert len(cells) == 4, row  # check id, what, if missing, how to get it
        assert all(cells), row
