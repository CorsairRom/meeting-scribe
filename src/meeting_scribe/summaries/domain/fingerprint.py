"""Fingerprint of the effective transcript a summary was made from (spec 7)."""

import hashlib
import json
from collections.abc import Sequence

from meeting_scribe.summaries.domain.request import SummaryLine


def input_fingerprint(lines: Sequence[SummaryLine]) -> str:
    """SHA-256 over a canonical JSON of ``(display name, start, end, text)`` per line.

    Provider, language and model are deliberately not part of it: staleness means the transcript
    changed (a rename, a merge, a reprocess), not that another provider could be used.
    """
    canonical = json.dumps(
        [[line.speaker, line.start, line.end, line.text] for line in lines],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
