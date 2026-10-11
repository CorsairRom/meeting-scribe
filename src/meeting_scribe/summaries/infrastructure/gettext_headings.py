"""Heading catalog adapter: gettext-style ``.po`` files read straight from the package (spec 11).

The English headings in the domain are the message ids. ``locale/<language>/LC_MESSAGES/
meeting_scribe.po`` maps them to a language; the source file is read at run time (no compile step,
no ``msgfmt``) so it ships as plain package data. Missing, unreadable or broken catalogs and
unknown languages give English headings, never an error.
"""

import ast
import re
from importlib.resources import files
from importlib.resources.abc import Traversable

from pydantic import ValidationError

from meeting_scribe.summaries.domain.headings import ENGLISH, Headings

DOMAIN = "meeting_scribe"
_LANGUAGE_CODE = re.compile(r"[a-z]{2,3}")
_MIN_QUOTED = 2


def default_locale_dir() -> Traversable:
    return files("meeting_scribe.summaries").joinpath("locale")


def load_catalog(language: str, locale_dir: Traversable | None = None) -> dict[str, str] | None:
    """The message id to translation map of a language; ``None`` when there is no usable catalog."""
    if _LANGUAGE_CODE.fullmatch(language) is None:
        return None
    root = locale_dir if locale_dir is not None else default_locale_dir()
    path = root.joinpath(language, "LC_MESSAGES", f"{DOMAIN}.po")
    try:
        return parse_po(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, SyntaxError):
        return None


def parse_po(text: str) -> dict[str, str]:
    """Read ``msgid``/``msgstr`` pairs (comments, continuation lines, escapes); skip empty."""
    entries: dict[str, str] = {}
    parts: dict[str, list[str]] = {"msgid": [], "msgstr": []}
    current: str | None = None

    def flush() -> None:
        msgid, msgstr = "".join(parts["msgid"]), "".join(parts["msgstr"])
        if msgid and msgstr:
            entries[msgid] = msgstr
        parts["msgid"], parts["msgstr"] = [], []

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        if not line:
            flush()
            current = None
            continue
        keyword, _, rest = line.partition(" ")
        if keyword in parts:
            if keyword == "msgid" and parts["msgstr"]:
                flush()
            current, line = keyword, rest.strip()
        if current is None:
            raise ValueError(f"unexpected line: {line[:20]!r}")
        parts[current].append(_unquote(line))
    flush()
    return entries


def _unquote(line: str) -> str:
    if len(line) < _MIN_QUOTED or line[0] != '"' or line[-1] != '"':
        raise ValueError(f"expected a quoted string: {line[:20]!r}")
    return str(ast.literal_eval(line))


class GettextHeadingCatalog:
    """Headings translated from the packaged catalogs; English for any other language."""

    def __init__(self, locale_dir: Traversable | None = None) -> None:
        self._locale_dir = locale_dir

    def headings_for(self, language: str) -> Headings:
        catalog = load_catalog(language, self._locale_dir)
        if not catalog:
            return ENGLISH
        values = {
            name: catalog.get(message, message) for name, message in ENGLISH.model_dump().items()
        }
        try:
            return Headings(**values)
        except ValidationError:
            return ENGLISH
