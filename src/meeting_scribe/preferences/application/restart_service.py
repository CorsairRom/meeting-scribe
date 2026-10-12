"""Restart the service through systemd, only when it is idle (spec ``preferences.md`` section 5).

Two steps so the reply can leave before the restart begins: ``check`` decides (busy, unreachable
or ``RESTARTING``) and ``perform`` runs after the reply is sent, looking at the activity once
more. A job interrupted by the restart resumes as on any start.
"""

import logging

from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.preferences.domain.errors import (
    SettingsRestartBusyError,
    SettingsRestartUnavailableError,
)
from meeting_scribe.preferences.domain.ports import ActivityProbe, ServiceRestarter

logger = logging.getLogger(__name__)

MANUAL_COMMAND = "systemctl --user restart meeting-scribe.service"


class RestartService:
    def __init__(self, activity: ActivityProbe, restarter: ServiceRestarter) -> None:
        self._activity = activity
        self._restarter = restarter

    async def check(self) -> Result[str, MeetingScribeError]:
        if self._activity.busy():
            return Failure(
                SettingsRestartBusyError(
                    "A recording, a processing job or a summary is active. Try again when it ends."
                )
            )
        if not await self._restarter.is_reachable():
            return Failure(
                SettingsRestartUnavailableError(
                    f"The service manager could not be reached. Run `{MANUAL_COMMAND}`."
                )
            )
        return Success("RESTARTING")

    async def perform(self) -> None:
        if self._activity.busy():
            logger.warning("Restart skipped: something became active after the reply.")
            return
        restarted = await self._restarter.restart()
        if isinstance(restarted, Failure):
            logger.error("Restart failed: %s", restarted.failure().error_code)
