"""``meeting-scribe prefetch-models``: download the models once, at install time (OQ-7).

The worker runs with ``HF_HUB_OFFLINE=1``, so this is the only moment processing needs the network
or the Hugging Face login. Both models are always attempted so one run reports every problem; the
fetchers are injected (the real ones live in the two ML backends), which keeps this module free of
ML imports and testable with fakes. The Hugging Face token is never read here and anything that
looks like one is scrubbed from the output.
"""

import re
import sys
from collections.abc import Callable
from typing import TextIO

from meeting_scribe.base.domain.errors import MeetingScribeError, ModelUnavailableError
from meeting_scribe.speakers.infrastructure.pyannote_diarizer import map_pyannote_error
from meeting_scribe.transcription.infrastructure.faster_whisper_transcriber import (
    map_whisper_error,
)

type Fetcher = Callable[[str], None]

_TOKEN = re.compile(r"hf_[A-Za-z0-9]+")
_ML_MISSING = "Install the ml extra (`uv sync --extra ml`, or run `make install`)."


def _scrub(text: str) -> str:
    return _TOKEN.sub("hf_***", text)


_RETRY = "meeting-scribe prefetch-models"


def _explain(error: MeetingScribeError, raw: Exception, name: str, setting: str) -> str:
    if isinstance(error, ModelUnavailableError):
        # The mapper's text is about the offline worker; at install time say what failed instead.
        text = (
            f"[{error.error_code}] Could not fetch '{name}' ({type(raw).__name__}). Check the "
            f"network connection, the model name ({setting}) and the Hugging Face cache "
            f"(HF_HOME), then retry: `{_RETRY}`."
        )
    else:
        text = f"[{error.error_code}] {error}"
    if isinstance(raw, ImportError):
        text += f" {_ML_MISSING}"
    return _scrub(text)


def prefetch_models(
    whisper_model: str,
    pipeline: str,
    fetch_whisper: Fetcher,
    fetch_pipeline: Fetcher,
    out: TextIO | None = None,
) -> int:
    """Fetch both models; 0 when both are cached afterwards, 1 otherwise."""
    stream = out or sys.stdout
    ready = 0
    # The gated pipeline goes first: a missing login fails in seconds, not after 1.6 GB.
    steps = (
        ("diarization pipeline", pipeline, fetch_pipeline, map_pyannote_error, "SPEAKERS_PIPELINE"),
        ("Whisper model", whisper_model, fetch_whisper, map_whisper_error, "TRANSCRIPTION_MODEL"),
    )
    for label, name, fetch, mapper, setting in steps:
        print(
            f"==> Downloading the {label} '{name}' (skipped files are already cached)", file=stream
        )
        stream.flush()
        try:
            fetch(name)
        except Exception as error:
            print(f"    FAILED {_explain(mapper(error, name), error, name, setting)}", file=stream)
        else:
            print("    ok", file=stream)
            ready += 1
    print(f"Models ready: {ready} of {len(steps)}.", file=stream)
    return 0 if ready == len(steps) else 1
