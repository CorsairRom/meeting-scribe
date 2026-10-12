"""Console script: ``meeting-scribe`` serves, ``meeting-scribe prefetch-models`` downloads models.

Kept apart from ``main`` on purpose: the service entry point must never reach the ML backends
(import contract), and the prefetch command is the one command that needs them. The service starts
the download as ``python -m meeting_scribe.cli prefetch-models --token-stdin --progress-json``.
"""

import argparse
import sys
from collections.abc import Sequence
from typing import TextIO

from meeting_scribe import main as service
from meeting_scribe.prefetch import check_models, prefetch_models
from meeting_scribe.settings import Settings, get_settings
from meeting_scribe.speakers.infrastructure.pyannote_backend import RealPyannoteBackend
from meeting_scribe.transcription.infrastructure.faster_whisper_backend import (
    FasterWhisperBackend,
)

PREFETCH = "prefetch-models"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meeting-scribe", description="Local GPU-accelerated meeting assistant."
    )
    commands = parser.add_subparsers(dest="command", metavar="[command]")
    prefetch = commands.add_parser(
        PREFETCH, help="download the Whisper model and the pyannote pipeline"
    )
    prefetch.add_argument(
        "--check",
        action="store_true",
        help="offline: print which models are cached as one JSON line; exit 0 when both are",
    )
    prefetch.add_argument(
        "--token-stdin",
        action="store_true",
        help="read the Hugging Face token from the first line of standard input (never argv)",
    )
    prefetch.add_argument(
        "--progress-json",
        action="store_true",
        help="print one JSON object per line (step, error, finished) instead of text",
    )
    return parser


def _check(settings: Settings) -> int:
    return check_models(
        settings.transcription.model,
        settings.speakers.pipeline,
        FasterWhisperBackend().is_model_cached,
        RealPyannoteBackend().is_pipeline_cached,
    )


def run(argv: Sequence[str] | None = None, stdin: TextIO | None = None) -> int:
    """Serve (the systemd unit passes no arguments) or run the named command."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command != PREFETCH:
        return service.run()
    if args.check and (args.token_stdin or args.progress_json):
        parser.error("--check cannot be combined with --token-stdin or --progress-json")
    settings = get_settings()
    if args.check:
        return _check(settings)
    token: str | None = None
    if args.token_stdin:
        token = (stdin or sys.stdin).readline().strip()
        if not token:
            print("No token was received on standard input.", file=sys.stderr)
            return 2
    return prefetch_models(
        settings.transcription.model,
        settings.speakers.pipeline,
        FasterWhisperBackend().download_model,
        RealPyannoteBackend().fetch_pipeline,
        token=token,
        progress_json=args.progress_json,
        discard_whisper=FasterWhisperBackend().discard_incomplete,
        discard_pipeline=RealPyannoteBackend().discard_incomplete,
    )


if __name__ == "__main__":
    sys.exit(run())
