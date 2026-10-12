"""The client name the cloud gateway sees: ``meeting-scribe/<version>`` and nothing else."""

import importlib.metadata

PACKAGE = "meeting-scribe"


def user_agent() -> str:
    try:
        version = importlib.metadata.version(PACKAGE)
    except importlib.metadata.PackageNotFoundError:
        version = "0"
    return f"{PACKAGE}/{version}"
