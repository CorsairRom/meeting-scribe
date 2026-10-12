"""The "Speech models" group of Preferences: the models' status, the token and the download.

The Hugging Face token goes from the field to the keyring port and nowhere else: it is not kept in
a state or on the presenter, never logged, and no gateway call takes one (the service reads it
from the keyring itself, ADR-011). The service downloads; this presenter only shows its status and
sends ``Download`` and ``CancelDownload``.

Replies are matched to what asked for them, so a late one never overrides a newer outcome:

- a status answer is dropped when anything newer happened (a signal, a reopening, a new press);
- a ``Download`` or ``CancelDownload`` reply counts only while its own press is still waiting;
- a keyring write still running is not forgotten by a close or a reopen: only its own end frees
  the field and Download, and a success after the close still means the token IS stored;
- what is only local (waiting for a start, cancelling) is cleared when the dialog closes and
  everything shown is read again from the service when it opens.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from meeting_scribe_review.core.errors import (
    INVALID_REPLY,
    KEYRING_STORE_FAILED,
    SERVICE_NOT_RUNNING,
    SERVICE_OUTDATED,
    ServiceError,
    message_for,
)
from meeting_scribe_review.core.gateway import ModelsGateway, TokenStore
from meeting_scribe_review.core.models import KeyStatus, ModelsStatus
from meeting_scribe_review.core.result import Err, Ok, Result

TERMS_URL = "https://hf.co/pyannote/speaker-diarization-community-1"
"""The page where the user accepts the pyannote terms: a human step (spec install 6)."""
TERMS_TEXT = (
    "The speaker model is gated: accepting its terms on Hugging Face and creating a token "
    "are steps only you can do."
)
SAVED_TEXT = "Token saved."
EMPTY_TEXT = "Enter the token."
_STORE_FAILED_TEXT = (
    "The token could not be stored in the keyring. Unlock the login keyring and try again."
)
TOKEN_TEXTS = {
    KeyStatus.UNKNOWN: "Checking the keyring...",
    KeyStatus.STORED: "Token stored",
    KeyStatus.MISSING: "No token stored",
    KeyStatus.LOCKED: "Keyring locked",
    KeyStatus.UNAVAILABLE: "Keyring unavailable",
}
LOADING_TEXT = "Checking the speech models..."
READY_TEXT = "Ready"
READY_DETAIL = "The speech models are downloaded."
PENDING_TEXT = "Models not downloaded"
PENDING_HINT_STORED = "Press Download models."
PENDING_HINT_MISSING = "Add your Hugging Face token above, then press Download models."
DOWNLOADING_TEXT = "Downloading"
FAILED_TEXT = "Download failed"
CANCELLED_TEXT = "Download cancelled"
CANCELLED_DETAIL = "Press Download models to resume. What was downloaded is kept."
STARTING_TEXT = "Starting the download..."
CANCELLING_TEXT = "Cancelling..."
ACTIVE_CODE = "MODELS_DOWNLOAD_ACTIVE"
_STATE_READY = "READY"
_STATE_PENDING = "PENDING"
_STATE_DOWNLOADING = "DOWNLOADING"
_STATE_FAILED = "FAILED"
_STATE_CANCELLED = "CANCELLED"


class ModelsPhase(StrEnum):
    LOADING = "loading"
    READY = "ready"
    PENDING = "pending"
    DOWNLOADING = "downloading"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNAVAILABLE = "unavailable"  # the service does not answer, or answers what is not understood


@dataclass(frozen=True, slots=True)
class ModelsState:
    phase: ModelsPhase = ModelsPhase.LOADING
    status_text: str = LOADING_TEXT
    detail: str = ""  # what to do next, or why it failed
    hint: str = ""  # a command to run, shown under the status
    show_progress: bool = False
    progress_text: str = ""
    progress_fraction: float | None = None  # completed steps over the total; None: the view pulses
    can_download: bool = False
    can_cancel: bool = False
    can_retry: bool = False  # a transient error: read the status again in place
    token_status: KeyStatus = KeyStatus.UNKNOWN
    token_text: str = TOKEN_TEXTS[KeyStatus.UNKNOWN]  # never the token itself
    token_problem: str = ""
    token_message: str = ""
    token_saving: bool = False
    can_save_token: bool = True


class ModelsPresenter:
    """Also the ``Models1`` listener: the gateway keeps it for as long as the window lives."""

    def __init__(
        self,
        gateway: ModelsGateway,
        tokens: TokenStore,
        on_change: Callable[[ModelsState], None],
    ) -> None:
        self._gateway = gateway
        self._tokens = tokens
        self._on_change = on_change
        self._open = False
        self._generation = 0  # bumped by whatever makes an older status answer stale
        self._phase = ModelsPhase.LOADING
        self._step = self._total = 0
        self._failure_code = ""
        self._problem: ServiceError | None = None
        self._requesting = False  # a press of Download whose reply has not arrived
        self._cancelling = False
        self._press = 0  # names the press (download or cancel) a reply belongs to
        self._token = KeyStatus.UNKNOWN
        self._token_problem = ""
        self._token_message = ""
        self._attempt = 0
        self._saving: int | None = None  # the attempt in flight, if any; only it decides the text
        self._token_version = 0  # bumped by a store: an older keyring lookup never undoes it
        self._state = ModelsState()
        gateway.watch_models(self)

    @property
    def state(self) -> ModelsState:
        return self._state

    # -- the dialog's life ------------------------------------------------------------------

    def open(self) -> None:
        """The dialog opens: read the status and the keyring again (they may have changed)."""
        self._open = True
        self._token_problem = self._token_message = ""
        self._reload()
        self.refresh_token()

    def close(self) -> None:
        """Forget what is only local; an answer still on its way is dropped or kept as a fact."""
        self._open = False
        self._generation += 1
        self._press += 1
        self._requesting = self._cancelling = False
        # ``_saving`` stays: the keyring write is still running and only its end clears it.

    def dispose(self) -> None:
        """The window is going away: stop listening to the service."""
        self._gateway.unwatch_models(self)

    # -- the service ------------------------------------------------------------------------

    def on_service_available(self, available: bool) -> None:
        if not self._open:
            return  # nothing to keep in step while no dialog is showing
        if available:
            self._reload()
            return
        self._generation += 1
        self._requesting = self._cancelling = False
        self._fail(ServiceError(SERVICE_NOT_RUNNING))

    def on_models_progress(self, model: str, step: int, total_steps: int) -> None:
        del model  # the step number says it all
        if not self._open:
            return
        self._generation += 1
        self._requesting = False
        self._problem = None
        self._phase = ModelsPhase.DOWNLOADING
        self._step, self._total = step, total_steps
        self._publish()

    def on_models_finished(self, state: str, error_code: str) -> None:
        if not self._open:
            return
        self._generation += 1
        self._requesting = self._cancelling = False
        self._problem = None
        if state == _STATE_READY:
            self._phase = ModelsPhase.READY
        elif state == _STATE_FAILED:
            self._phase, self._failure_code = ModelsPhase.FAILED, error_code
        elif state == _STATE_CANCELLED:
            self._phase = ModelsPhase.CANCELLED
        else:  # a state a newer service sends: ask what is true
            self._reload()
            return
        self._publish()

    # -- status -----------------------------------------------------------------------------

    def _reload(self) -> None:
        self._generation += 1
        generation = self._generation
        self._requesting = self._cancelling = False
        self._problem = None
        self._phase = ModelsPhase.LOADING
        self._step = self._total = 0
        self._publish()
        self._gateway.models_status(lambda result: self._status_loaded(generation, result))

    def _status_loaded(self, generation: int, result: Result[ModelsStatus, ServiceError]) -> None:
        if generation != self._generation:
            return  # something newer happened since it was asked
        if isinstance(result, Err):
            self._fail(result.error)
            return
        status = result.value
        self._problem = None
        self._step, self._total = status.step, status.total_steps
        self._failure_code = status.failure_code
        phase = {
            _STATE_READY: ModelsPhase.READY,
            _STATE_PENDING: ModelsPhase.PENDING,
            _STATE_DOWNLOADING: ModelsPhase.DOWNLOADING,
            _STATE_FAILED: ModelsPhase.FAILED,
        }.get(status.state)
        if phase is None:
            self._fail(ServiceError(INVALID_REPLY))
            return
        self._phase = phase
        self._publish()

    def retry(self) -> None:
        """Read the status again after a transient error (never for a missing or older service)."""
        if self._state.can_retry:
            self._reload()

    def _fail(self, error: ServiceError) -> None:
        self._phase = ModelsPhase.UNAVAILABLE
        self._problem = error
        self._publish()

    # -- download and cancel ----------------------------------------------------------------

    def download(self) -> None:
        """A press on Download models: the only way a download starts."""
        if not self._state.can_download:
            return
        self._generation += 1
        self._press += 1
        press = self._press
        self._requesting = True
        self._problem = None
        self._step = self._total = 0
        self._publish()
        self._gateway.download_models(lambda result: self._download_replied(press, result))

    def _download_replied(self, press: int, result: Result[str, ServiceError]) -> None:
        if not self._requesting or press != self._press:
            return  # a signal already told the outcome, or the dialog closed meanwhile
        self._requesting = False
        if isinstance(result, Err):
            self._download_refused(result.error)
            return
        if result.value == _STATE_READY:  # nothing was missing
            self._phase = ModelsPhase.READY
        elif result.value == _STATE_DOWNLOADING:
            self._phase = ModelsPhase.DOWNLOADING
        else:
            self._reload()
            return
        self._publish()

    def _download_refused(self, error: ServiceError) -> None:
        if error.code == ACTIVE_CODE:  # one is already running: follow it
            self._reload()
        elif error.code == SERVICE_NOT_RUNNING:
            self._fail(error)
        else:
            self._problem = error  # the phase stays: the user may try again
            self._publish()

    def cancel(self) -> None:
        if not self._state.can_cancel:
            return
        self._press += 1
        press = self._press
        self._cancelling = True
        self._publish()
        self._gateway.cancel_download(lambda result: self._cancel_replied(press, result))

    def _cancel_replied(self, press: int, result: Result[bool, ServiceError]) -> None:
        if not self._cancelling or press != self._press:
            return
        if isinstance(result, Err):
            self._cancelling = False
            self._problem = result.error
            self._publish()
        elif not result.value:  # nothing was running any more: ask what is true
            self._reload()
        # True: the outcome arrives as ``ModelsFinished(CANCELLED)``

    # -- the token --------------------------------------------------------------------------

    def refresh_token(self) -> None:
        version = self._token_version
        self._tokens.is_stored(lambda result: self._token_known(version, result))

    def _token_known(self, version: int, result: Result[KeyStatus, ServiceError]) -> None:
        if version != self._token_version:
            return  # a store happened since: its outcome is newer than this lookup
        self._token = result.value if isinstance(result, Ok) else KeyStatus.UNAVAILABLE
        self._publish()

    def submit_token(self, raw: str) -> None:
        if self._saving is not None:
            return
        secret = raw.strip()
        self._token_message = ""
        if not secret:
            self._token_problem = EMPTY_TEXT
            self._publish()
            return
        self._attempt += 1
        attempt = self._attempt
        self._saving = attempt
        self._token_version += 1
        self._token_problem = ""
        self._publish()
        self._tokens.store(secret, lambda result: self._stored(attempt, result))

    def _stored(self, attempt: int, result: Result[None, ServiceError]) -> None:
        failed = isinstance(result, Err)
        if attempt != self._saving:
            # Stale (the dialog closed, or a newer attempt took over). It never decides the text of
            # a newer attempt, but a success still means the token IS in the keyring.
            if not failed:
                self._token = KeyStatus.STORED
                self._token_version += 1
                self._publish()
            return
        self._saving = None
        if isinstance(result, Err):
            self._token_problem = _token_problem(result.error)
        else:
            self._token = KeyStatus.STORED
            self._token_version += 1
            self._token_message = SAVED_TEXT
        self._publish()

    # -- the state shown --------------------------------------------------------------------

    def _publish(self) -> None:
        self._state = self._build()
        self._on_change(self._state)

    def _build(self) -> ModelsState:
        phase = ModelsPhase.DOWNLOADING if self._requesting else self._phase
        saving = self._saving is not None
        status_text, detail, hint = self._words(phase)
        running = phase is ModelsPhase.DOWNLOADING
        return ModelsState(
            phase=phase,
            status_text=status_text,
            detail=detail,
            hint=hint,
            show_progress=running,
            progress_text=self._progress_text() if running else "",
            progress_fraction=self._fraction() if running and not self._requesting else None,
            can_download=phase in _RETRYABLE and not saving,
            can_cancel=running and not self._requesting and not self._cancelling,
            can_retry=phase is ModelsPhase.UNAVAILABLE and self._transient(),
            token_status=self._token,
            token_text=TOKEN_TEXTS[self._token],
            token_problem=self._token_problem,
            token_message=self._token_message,
            token_saving=saving,
            can_save_token=not saving,
        )

    def _words(self, phase: ModelsPhase) -> tuple[str, str, str]:
        """The headline, the line under it and the command to run, for a phase."""
        problem = message_for(self._problem) if self._problem is not None else None
        if phase is ModelsPhase.UNAVAILABLE:
            text = message_for(self._problem or ServiceError(INVALID_REPLY))
            return text.text, "", text.hint
        detail = {
            ModelsPhase.READY: READY_DETAIL,
            ModelsPhase.PENDING: PENDING_HINT_STORED
            if self._token is KeyStatus.STORED
            else PENDING_HINT_MISSING,
            ModelsPhase.FAILED: message_for(ServiceError(self._failure_code)).text,
            ModelsPhase.CANCELLED: CANCELLED_DETAIL,
        }.get(phase, "")
        if problem is not None:  # the last action failed: that is what the user needs to read
            return _HEADLINES[phase], problem.text, problem.hint
        return _HEADLINES[phase], detail, ""

    def _transient(self) -> bool:
        """A failed status that may pass: not a service that is missing or older than the app."""
        return self._problem is not None and self._problem.code not in _NOT_TRANSIENT

    def _progress_text(self) -> str:
        if self._cancelling:
            return CANCELLING_TEXT
        if self._requesting or self._step <= 0 or self._total <= 0:
            return STARTING_TEXT
        return f"Downloading step {self._step} of {self._total}"

    def _fraction(self) -> float | None:
        if self._cancelling or self._step <= 0 or self._total <= 0:
            return None
        return (self._step - 1) / self._total


_NOT_TRANSIENT = frozenset({SERVICE_NOT_RUNNING, SERVICE_OUTDATED})
_RETRYABLE = frozenset({ModelsPhase.PENDING, ModelsPhase.FAILED, ModelsPhase.CANCELLED})
_HEADLINES = {
    ModelsPhase.LOADING: LOADING_TEXT,
    ModelsPhase.READY: READY_TEXT,
    ModelsPhase.PENDING: PENDING_TEXT,
    ModelsPhase.DOWNLOADING: DOWNLOADING_TEXT,
    ModelsPhase.FAILED: FAILED_TEXT,
    ModelsPhase.CANCELLED: CANCELLED_TEXT,
    ModelsPhase.UNAVAILABLE: "",
}


def _token_problem(error: ServiceError) -> str:
    if error.code == KEYRING_STORE_FAILED:
        return _STORE_FAILED_TEXT
    return message_for(error).text
