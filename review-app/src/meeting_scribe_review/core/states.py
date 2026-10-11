"""Processing states of a meeting (``Meetings1.List``) and how the app words them."""

STATE_DONE = "DONE"
STATE_FAILED = "FAILED"
STATE_UNSUPPORTED = "UNSUPPORTED"

_LABELS = {
    "QUEUED": "Waiting",
    "TRANSCRIBING": "Transcribing",
    "DIARIZING": "Identifying speakers",
    "EXTRACTING_SAMPLES": "Extracting voice samples",
    STATE_DONE: "Ready",
    STATE_FAILED: "Failed",
    STATE_UNSUPPORTED: "Unsupported version",
}


def state_label(state: str) -> str:
    """A short label; an unknown state (a newer service) is shown as it came."""
    return _LABELS.get(state, state)


def is_processing(state: str) -> bool:
    return state in {"QUEUED", "TRANSCRIBING", "DIARIZING", "EXTRACTING_SAMPLES"}
