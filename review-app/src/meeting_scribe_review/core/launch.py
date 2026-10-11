"""Command-line options of the app, as plain values (the ``ui`` package unpacks the GLib dict)."""

from collections.abc import Mapping
from typing import Any

MEETING_OPTION = "meeting"


def meeting_id_from_options(options: Mapping[str, Any]) -> str:
    """The meeting named by ``--meeting ID``; empty means "the newest reviewed one" (AC-13)."""
    value = options.get(MEETING_OPTION)
    return value.strip() if isinstance(value, str) else ""
