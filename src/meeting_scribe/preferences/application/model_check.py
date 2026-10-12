"""Checks a changed model against the model list, when the list is available (AC-19).

An unavailable list (Ollama stopped, no key, offline) never blocks a save: the value is accepted
as typed by the client. Messages carry the field and a next step, never the model name.
"""

from returns.result import Failure, Result, Success

from meeting_scribe.preferences.domain.fields import FieldCode, FieldProblem
from meeting_scribe.preferences.domain.ports import ModelCatalog

NOT_LISTED = "This model is not in the provider's list. Pick one from the list."
NOT_SUPPORTED = "This model is not supported yet. Pick another one."


class ModelCheck:
    """A ``FieldCheck`` for one model field: ``scope`` is ``local`` or ``cloud``."""

    def __init__(self, catalog: ModelCatalog, scope: str, key: str) -> None:
        self._catalog = catalog
        self._scope = scope
        self._key = key

    def __call__(self, raw: str) -> Result[str, FieldProblem]:
        # Never a fetch here: ``Set`` holds its lock. No fresh list means the offline rule.
        listing = self._catalog.cached_models(self._scope)
        if listing is None or not listing.available:
            return Success(raw)
        row = next((row for row in listing.models if row.id == raw), None)
        if row is None:
            return Failure(FieldProblem(self._key, FieldCode.INVALID, NOT_LISTED))
        if not row.supported:
            return Failure(FieldProblem(self._key, FieldCode.INVALID, NOT_SUPPORTED))
        return Success(raw)
