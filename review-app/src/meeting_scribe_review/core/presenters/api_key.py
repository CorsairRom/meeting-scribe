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
        self._stored_earlier = False  # a stale attempt stored the key while a newer one was saving

    @property
    def state(self) -> ApiKeyState:
        return self._state

    def edit(self) -> None:
        self._saving = None
        self._stored_earlier = False  # an attempt still running is now stale
        self._publish(ApiKeyState(ApiKeyStatus.EDITING))

    def cancel(self) -> None:
        self._saving = None
        self._stored_earlier = False
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
        failed = isinstance(result, Err)
        if attempt != self._saving:
            # Stale: it never overrides a newer attempt, but a success still means the key IS in
            # the keyring, so the fact is kept (newer attempt saving) or shown (none in flight).
            if not failed:
                self._note_stored_key()
            return
        self._saving = None
        stored_earlier, self._stored_earlier = self._stored_earlier, False
        self._publish(_outcome(result, stored_earlier=stored_earlier))

    def _note_stored_key(self) -> None:
        if self._saving is not None:
            self._stored_earlier = True  # no state change; the newer attempt reports it
        elif self._state.status is not ApiKeyStatus.SAVED:
            # Replaces any old problem text; the user's current edit is neither closed nor claimed.
            self._publish(replace(self._state, problem="", message=KEY_STORED_TEXT))

    def _publish(self, state: ApiKeyState) -> None:
        self._state = state
        self._on_change(state)


def _outcome(result: Result[None, ServiceError], *, stored_earlier: bool) -> ApiKeyState:
    """Saved, or the attempt's error plus a note when an earlier attempt stored the key."""
    if isinstance(result, Err):
        message = KEY_STORED_TEXT if stored_earlier else ""
        return ApiKeyState(
            ApiKeyStatus.EDITING, problem=message_for(result.error).text, message=message
        )
    return ApiKeyState(ApiKeyStatus.SAVED, message=SAVED_TEXT)
