"""Composition root: resolves settings, configures logging and wires the application."""

import logging
from dataclasses import dataclass

from meeting_scribe.base.infrastructure.logging import configure_logging
from meeting_scribe.settings import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Application:
    """Wired application. Later phases add controllers and adapters here."""

    settings: Settings


def create_app(settings: Settings | None = None) -> Application:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    return Application(settings=settings)


def run() -> None:
    create_app()
    logger.info("meeting-scribe started with no transport yet (D-Bus arrives in F1)")
