"""Worker process entry point: ``python -m meeting_scribe.worker <stage>`` (spec 6.1, ADR-007).

A second composition root next to ``main``. This skeleton speaks the protocol and rejects what it
cannot run; the real stages land in F3.2 (transcribe) and F3.3 (diarize). It must stay importable
without any ML library.
"""

import json
import os
import sys
from typing import IO

STAGES = ("transcribe", "diarize")
EXIT_ERROR = 1


def open_event_stream() -> IO[str]:
    """Keep the real stdout for protocol events and point fd 1 at stderr (spec 6.2).

    Call before importing any library: a stray ``print`` or progress bar then lands in the log
    instead of corrupting the event stream.
    """
    sys.stdout.flush()
    events = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    return events


def _emit(events: IO[str], payload: dict[str, object]) -> None:
    events.write(json.dumps(payload, ensure_ascii=False) + "\n")
    events.flush()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    events = open_event_stream()
    stage = args[0] if args else ""
    if stage not in STAGES:
        _emit(
            events,
            {
                "event": "error",
                "error_code": "WORKER_UNKNOWN_STAGE",
                "message": f"Unknown worker stage {stage!r}; expected one of {', '.join(STAGES)}.",
            },
        )
        return EXIT_ERROR
    _emit(
        events,
        {
            "event": "error",
            "error_code": "WORKER_STAGE_UNAVAILABLE",
            "message": f"The {stage} stage is not implemented in this build.",
        },
    )
    return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
