"""Console script: ``meeting-scribe`` serves, ``meeting-scribe prefetch-models`` downloads models.

Kept apart from ``main`` on purpose: the service entry point must never reach the ML backends
(import contract), and the prefetch command is the one command that needs them.
"""

import argparse
from collections.abc import Sequence

from meeting_scribe import main as service
from meeting_scribe.prefetch import prefetch_models
from meeting_scribe.settings import get_settings
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
    commands.add_parser(PREFETCH, help="download the Whisper model and the pyannote pipeline")
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    """Serve (the systemd unit passes no arguments) or run the named command."""
    args = _parser().parse_args(argv)
    if args.command == PREFETCH:
        settings = get_settings()
        return prefetch_models(
            settings.transcription.model,
            settings.speakers.pipeline,
            FasterWhisperBackend().download_model,
            RealPyannoteBackend().fetch_pipeline,
        )
    return service.run()
