"""``ListModels``: the models a scope offers, for the Preferences pickers (spec preferences.md).

The cloud list needs the API key, which is read from the keyring for this call only and handed to
the adapter; it is never returned, stored or logged (BR-005).
"""

from returns.result import Failure, Result

from meeting_scribe.summaries.domain.errors import SummaryError, SummaryModelsUnavailableError
from meeting_scribe.summaries.domain.ports import (
    CloudKeyStore,
    CloudModelSource,
    LocalModelSource,
    ModelEntry,
)

LOCAL = "local"
CLOUD = "cloud"


class ListModels:
    def __init__(
        self, local: LocalModelSource, cloud: CloudModelSource, keys: CloudKeyStore
    ) -> None:
        self._local = local
        self._cloud = cloud
        self._keys = keys

    def execute(self, scope: str) -> Result[list[ModelEntry], SummaryError]:
        if scope == LOCAL:
            return self._local.list_models()
        if scope == CLOUD:
            key = self._keys.get_key()
            if isinstance(key, Failure):
                return key
            return self._cloud.list_models(key.unwrap())
        return Failure(SummaryModelsUnavailableError("Unknown model scope."))
