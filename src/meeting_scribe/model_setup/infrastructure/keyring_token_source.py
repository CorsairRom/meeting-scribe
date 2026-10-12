"""The Hugging Face token from the GNOME Keyring, over the freedesktop Secret Service (ADR-011).

The review app stores the item through libsecret (application ``meeting-scribe``, key
``huggingface-token``); the service reads it here, on demand, inside the download job, and hands
it to the download child on stdin. The token is not a setting and never crosses D-Bus. A locked
keyring is reported, never unlocked: that would pop a prompt from the service. A missing item or
no Secret Service at all is not an error: the ambient Hugging Face login is used instead.
"""

import logging

from pydantic import SecretStr
from returns.result import Failure, Result, Success

from meeting_scribe.base.infrastructure.secret_service import (
    DEFAULT_TIMEOUT_SECONDS,
    SecretLockedError,
    SecretMissingError,
    SecretServiceReader,
    SecretUnavailableError,
)
from meeting_scribe.model_setup.domain.errors import ModelsTokenKeyringLockedError

logger = logging.getLogger(__name__)

ATTRIBUTES = {"application": "meeting-scribe", "key": "huggingface-token"}
LABEL = "Meeting Scribe Hugging Face token"
_LOCKED_MESSAGE = "The GNOME Keyring is locked. Unlock the login keyring and press Download again."


class KeyringTokenSource:
    def __init__(
        self, bus_address: str | None = None, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        """``bus_address=None`` is the user's session bus; tests pass their private bus."""
        self._reader = SecretServiceReader(ATTRIBUTES, bus_address, timeout_seconds)

    def token_stored(self) -> bool:
        try:
            return self._reader.exists()
        except SecretUnavailableError:
            return False

    def read_token(self) -> Result[SecretStr | None, ModelsTokenKeyringLockedError]:
        try:
            return Success(SecretStr(self._reader.read()))
        except SecretLockedError:
            return Failure(ModelsTokenKeyringLockedError(_LOCKED_MESSAGE))
        except (SecretMissingError, SecretUnavailableError):
            logger.info("No Hugging Face token in the keyring; the ambient login is used.")
            return Success(None)
