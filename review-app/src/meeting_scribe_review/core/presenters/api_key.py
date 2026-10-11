"""The "Set API key" flow (spec section 8a, BR-005).

The key goes from the field to the keyring port and nowhere else: it is not kept in a state or on
the presenter, never logged, and never sent to the service. A failed store reopens the editor
empty, so the user types it again.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum

from meeting_scribe_review.core.errors import ServiceError, message_for
from meeting_scribe_review.core.gateway import ApiKeyStore
from meeting_scribe_review.core.result import Err, Result

SAVED_TEXT = "API key saved. Press Generate summary to try again."
EMPTY_TEXT = "Enter the API key."
KEY_STORED_TEXT = "An API key was stored in the keyring."


class ApiKeyStatus(StrEnum):
    IDLE = "idle"
    EDITING = "editing"
    SAVING = "saving"
    SAVED = "saved"


@dataclass(frozen=True, slots=True)
class ApiKeyState:
    status: ApiKeyStatus = ApiKeyStatus.IDLE
    problem: str = ""
    message: str = ""


class ApiKeyPresenter:
    def __init__(self, store: ApiKeyStore, on_change: Callable[[ApiKeyState], None]) -> None:
        self._store = store
        self._on_change = on_change
        self._state = ApiKeyState()
        self._attempt = 0  # bumped per submit: names the attempt a store result belongs to
        self._saving: int | None = None  # the attempt in flight, if any; only it decides the state

    @property
    def state(self) -> ApiKeyState:
        return self._state

    def edit(self) -> None:
        self._saving = None  # an attempt still running is now stale
        self._publish(ApiKeyState(ApiKeyStatus.EDITING))

    def cancel(self) -> None:
        self._saving = None
        self._publish(ApiKeyState())

    def submit(self, raw: str) -> None:
        if self._state.status is not ApiKeyStatus.EDITING:
            return
        secret = raw.strip()
        if not secret:
            self._publish(ApiKeyState(ApiKeyStatus.EDITING, problem=EMPTY_TEXT))
            return
        self._attempt += 1
        attempt = self._attempt
        self._saving = attempt
        self._publish(ApiKeyState(ApiKeyStatus.SAVING))
        self._store.store(secret, lambda result: self._stored(attempt, result))

    def _stored(self, attempt: int, result: Result[None, ServiceError]) -> None:
        if attempt != self._saving:
            # A stale result never overrides a newer attempt that is still saving. With none in
            # flight, a stale success still means the key IS in the keyring: say so, without
            # claiming the user's current edit was saved and without closing it.
            if (
                self._saving is None
                and not isinstance(result, Err)
                and self._state.status is not ApiKeyStatus.SAVED
            ):
                self._publish(replace(self._state, message=KEY_STORED_TEXT))
            return
        self._saving = None
        if isinstance(result, Err):
            problem = message_for(result.error).text
            self._publish(ApiKeyState(ApiKeyStatus.EDITING, problem=problem))
        else:
            self._publish(ApiKeyState(ApiKeyStatus.SAVED, message=SAVED_TEXT))

    def _publish(self, state: ApiKeyState) -> None:
        self._state = state
        self._on_change(state)
