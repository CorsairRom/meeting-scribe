"""Whether the models are cached: ``prefetch-models --check`` run as a child, offline and bounded.

The service never imports the ML libraries, so the check runs in a child that does. It needs no
network and no login (``HF_HUB_OFFLINE=1``, no token variable). Any failure to answer, a timeout
included, means "not cached": the status then says PENDING and a download can proceed.
"""

import json
import logging
import signal
import subprocess
from collections.abc import Mapping, Sequence

from meeting_scribe.model_setup.domain.models import NOT_CACHED, CacheStatus
from meeting_scribe.model_setup.infrastructure.child_process import (
    DEFAULT_COMMAND,
    REAP_SECONDS,
    child_environment,
    signal_group,
)

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_COMMAND", "SubprocessCacheProbe"]


class SubprocessCacheProbe:
    def __init__(
        self,
        timeout_seconds: float = 30.0,
        command: Sequence[str] = DEFAULT_COMMAND,
        extra_env: Mapping[str, str] | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._command = tuple(command)
        self._env = child_environment(offline=True, drop_ambient_token=True, extra=extra_env)

    def check(self) -> CacheStatus:
        try:
            process = subprocess.Popen(  # noqa: S603 - fixed argv, never a shell
                [*self._command, "--check"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=self._env,
                start_new_session=True,
            )
        except OSError as error:
            logger.warning("Cannot start the model cache check: %s", type(error).__name__)
            return NOT_CACHED
        try:
            out, _ = process.communicate(timeout=self._timeout)
        except subprocess.TimeoutExpired:
            logger.warning("The model cache check did not answer within %.0f s.", self._timeout)
            signal_group(process, signal.SIGKILL)  # it only reads, there is nothing to clean up
            process.communicate(timeout=REAP_SECONDS)
            return NOT_CACHED
        return _parse(out)


def _parse(raw: bytes) -> CacheStatus:
    lines = [line for line in raw.decode("utf-8", errors="replace").splitlines() if line.strip()]
    if not lines:
        return NOT_CACHED
    try:
        found = json.loads(lines[-1])
    except ValueError:
        return NOT_CACHED
    whisper = found.get("whisper") if isinstance(found, dict) else None
    pipeline = found.get("pipeline") if isinstance(found, dict) else None
    if not (isinstance(whisper, bool) and isinstance(pipeline, bool)):
        return NOT_CACHED
    return CacheStatus(whisper_cached=whisper, pipeline_cached=pipeline)
