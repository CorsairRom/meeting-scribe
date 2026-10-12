"""``meeting-scribe prefetch-models``: the only step of processing that needs the network (OQ-7).

The worker runs with ``HF_HUB_OFFLINE=1`` and no token, so downloading is the only moment the
network or a Hugging Face login is involved. The service runs this command as a child process
(``--token-stdin --progress-json``); a developer can run it by hand. Both models are always
attempted so one run reports every problem; the fetchers and cache probes are injected (the real
ones live in the two ML backends), which keeps this module free of ML imports and testable with
fakes. The token, when given, comes from the caller (standard input); it is never read from the
environment or a file here, and it and anything that looks like one is scrubbed from the output.
"""

import json
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
type PipelineFetcher = Callable[[str, str | None], None]
"""``(pipeline, token)``: with no token the ambient Hugging Face login is used."""
type CacheProbe = Callable[[str], bool]

_TOKEN = re.compile(r"hf_[A-Za-z0-9]+")
_ML_MISSING = "Install the ml extra (`uv sync --extra ml`, or run `make install`)."


def _scrub(text: str, token: str | None = None) -> str:
    """Hide anything shaped like a token, and the exact token even when it is not."""
    if token:
        text = text.replace(token, "hf_***")
    return _TOKEN.sub("hf_***", text)


_RETRY = "meeting-scribe prefetch-models"


def _explain(
    error: MeetingScribeError, raw: Exception, name: str, setting: str, token: str | None
) -> str:
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
    return _scrub(text, token)


def _emit(stream: TextIO, event: dict[str, object]) -> None:
    print(json.dumps(event), file=stream)
    stream.flush()  # the service reads these lines live


def prefetch_models(
    whisper_model: str,
    pipeline: str,
    fetch_whisper: Fetcher,
    fetch_pipeline: PipelineFetcher,
    out: TextIO | None = None,
    *,
    token: str | None = None,
    progress_json: bool = False,
) -> int:
    """Fetch both models; 0 when both are cached afterwards, 1 otherwise.

    ``token`` goes to the gated pipeline only (the Whisper model is public). With
    ``progress_json`` stdout is one JSON object per line and nothing else: a ``step`` event before
    each model, an ``error`` event per failure (code and scrubbed text), a final ``finished``.
    """
    stream = out or sys.stdout
    ready = 0
    # The gated pipeline goes first: a missing login fails in seconds, not after 1.6 GB.
    steps = (
        (
            "pipeline",
            "diarization pipeline",
            pipeline,
            lambda name: fetch_pipeline(name, token),
            map_pyannote_error,
            "SPEAKERS_PIPELINE",
        ),
        (
            "whisper",
            "Whisper model",
            whisper_model,
            fetch_whisper,
            map_whisper_error,
            "TRANSCRIPTION_MODEL",
        ),
    )
    for number, (key, label, name, fetch, mapper, setting) in enumerate(steps, start=1):
        if progress_json:
            _emit(
                stream, {"event": "step", "model": key, "step": number, "total_steps": len(steps)}
            )
        else:
            print(
                f"==> Downloading the {label} '{name}' (skipped files are already cached)",
                file=stream,
            )
            stream.flush()
        try:
            fetch(name)
        except Exception as error:
            mapped = mapper(error, name)
            text = _explain(mapped, error, name, setting, token)
            if progress_json:
                _emit(
                    stream,
                    {
                        "event": "error",
                        "model": key,
                        "error_code": mapped.error_code,
                        "message": text,
                    },
                )
            else:
                print(f"    FAILED {text}", file=stream)
        else:
            if not progress_json:
                print("    ok", file=stream)
            ready += 1
    if progress_json:
        _emit(stream, {"event": "finished", "ready": ready == len(steps)})
    else:
        print(f"Models ready: {ready} of {len(steps)}.", file=stream)
    return 0 if ready == len(steps) else 1


def check_models(
    whisper_model: str,
    pipeline: str,
    whisper_cached: CacheProbe,
    pipeline_cached: CacheProbe,
    out: TextIO | None = None,
) -> int:
    """Offline: print ``{"whisper": bool, "pipeline": bool}``; 0 when both are cached, else 1.

    A probe that raises counts as "not cached": the check never fails loudly and never downloads.
    """
    stream = out or sys.stdout
    found = {
        "whisper": _probe(whisper_cached, whisper_model),
        "pipeline": _probe(pipeline_cached, pipeline),
    }
    print(json.dumps(found), file=stream)
    return 0 if all(found.values()) else 1


def _probe(cached: CacheProbe, name: str) -> bool:
    try:
        return bool(cached(name))
    except Exception:
        return False
