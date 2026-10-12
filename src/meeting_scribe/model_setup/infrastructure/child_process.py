"""Small helpers for the two child processes (cache probe and download): environment and stopping.

Children lead their own process group (``start_new_session``) and are stopped by group id, never by
name (GO-007): SIGTERM, a grace period, then SIGKILL, every wait bounded.
"""

import contextlib
import logging
import os
import re
import signal
import subprocess
import sys
from collections.abc import Mapping

logger = logging.getLogger(__name__)

DEFAULT_COMMAND = (sys.executable, "-m", "meeting_scribe.cli", "prefetch-models")
REAP_SECONDS = 5.0
_TOKEN = re.compile(r"hf_[A-Za-z0-9]+")
TOKEN_VARIABLES = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN")
OFFLINE_VARIABLES = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")


def scrub(text: str, token: str | None = None) -> str:
    """Hide anything shaped like a Hugging Face token, and the exact token even when it is not."""
    if token:
        text = text.replace(token, "hf_***")
    return _TOKEN.sub("hf_***", text)


def child_environment(
    *,
    offline: bool,
    drop_ambient_token: bool,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """The child's environment: the service's own, minus what must not be inherited.

    The token itself is never put here (it travels on stdin). ``drop_ambient_token`` removes the
    ambient token variables when a keyring token is passed, so the keyring token wins.
    """
    env = dict(os.environ)
    for name in OFFLINE_VARIABLES:
        env.pop(name, None)
    if drop_ambient_token or offline:
        for name in TOKEN_VARIABLES:
            env.pop(name, None)
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    env["PYANNOTE_METRICS_ENABLED"] = "false"
    if offline:
        env["HF_HUB_OFFLINE"] = "1"
    env.update(extra or {})
    return env


def signal_group(process: subprocess.Popen[bytes], sig: signal.Signals) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, sig)  # the child leads its own group (start_new_session)


def terminate(process: subprocess.Popen[bytes], grace_seconds: float) -> None:
    """SIGTERM the group, wait the grace period, then SIGKILL; bounded throughout."""
    if process.poll() is not None:
        return
    signal_group(process, signal.SIGTERM)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=grace_seconds)
    if process.poll() is not None:
        return
    logger.warning("Download child pid %d ignored SIGTERM; sending SIGKILL.", process.pid)
    signal_group(process, signal.SIGKILL)
    try:
        process.wait(timeout=REAP_SECONDS)
    except subprocess.TimeoutExpired:
        logger.error("Download child pid %d is still alive after SIGKILL.", process.pid)
