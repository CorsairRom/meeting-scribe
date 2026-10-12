"""The cloud API key from the GNOME Keyring, over the freedesktop Secret Service (ADR-009).

Called from the summary job thread, which has no event loop. The lookup itself lives in the shared
kernel (``base.infrastructure.secret_service``, also used for the Hugging Face token); this adapter
only gives it the key's attributes and turns its outcomes into summary errors. A locked keyring is
reported, never unlocked. Nothing here logs or quotes the secret.
"""

from pydantic import SecretStr
from returns.result import Failure, Result, Success

from meeting_scribe.base.infrastructure.secret_service import (
    DEFAULT_TIMEOUT_SECONDS,
    SecretLockedError,
    SecretMissingError,
    SecretServiceReader,
    SecretUnavailableError,
)
from meeting_scribe.summaries.domain.errors import (
    SummaryCloudKeyLockedError,
    SummaryCloudKeyMissingError,
    SummaryCloudKeyUnavailableError,
    SummaryError,
)

ATTRIBUTES = {"application": "meeting-scribe", "key": "opencode-api-key"}
LABEL = "Meeting Scribe OpenCode API key"
_LOCKED_MESSAGE = "The GNOME Keyring is locked. Unlock the login keyring and try again."
_UNAVAILABLE_MESSAGE = (
    "The GNOME Keyring service is not reachable (no Secret Service on the session bus). "
    "Check that gnome-keyring is running."
)


def missing_message() -> str:
    """The next step for a missing key, built from the label and attributes the lookup uses."""
    attributes = " ".join(f"{name} {value}" for name, value in ATTRIBUTES.items())
    return (
        "The cloud API key is not stored in the GNOME Keyring. Store it with "
        f'`secret-tool store --label="{LABEL}" {attributes}` '
        "(see docs/runbooks/store-cloud-api-key.md)."
    )


class SecretServiceKeyStore:
    def __init__(
        self, bus_address: str | None = None, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        """``bus_address=None`` is the user's session bus; tests pass their private bus."""
        self._reader = SecretServiceReader(ATTRIBUTES, bus_address, timeout_seconds)

    def get_key(self) -> Result[SecretStr, SummaryError]:
        try:
            secret = self._reader.read()
        except SecretLockedError:
            return Failure(SummaryCloudKeyLockedError(_LOCKED_MESSAGE))
        except SecretMissingError:
            return Failure(SummaryCloudKeyMissingError(missing_message()))
        except SecretUnavailableError:
            return Failure(SummaryCloudKeyUnavailableError(_UNAVAILABLE_MESSAGE))
        return Success(SecretStr(secret))
