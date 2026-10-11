"""The Summary view of the open meeting (AC-17, AC-18).

One "Generate summary" button uses the provider the service marks as default; a cloud provider
asks for confirmation first (``on_confirm``) and is sent only through ``send``. The summary text,
its title and the provider are private user data: they live in the state and are never logged.
Replies are matched to the load that asked for them, so a late reply for a meeting already left is
dropped.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from meeting_scribe_review.core.errors import (
    SERVICE_NOT_RUNNING,
    ServiceError,
    message_for,
)
from meeting_scribe_review.core.gateway import SummariesGateway
from meeting_scribe_review.core.models import ProviderRow, SummaryDocument, SummaryInfo
from meeting_scribe_review.core.presenters.cloud_confirmation import ConfirmationRequest
from meeting_scribe_review.core.result import Err, Result
from meeting_scribe_review.core.states import STATE_DONE

BUTTON_TEXT = "Generate summary"
STALE_TEXT = (
    "This summary is out of date: the speakers or the transcript changed after it was made."
)
COPIED_TEXT = "Summary copied"
SAVED_TEXT = "Summary saved"
SAVE_FAILED_TEXT = "The summary could not be saved."
NOT_FOUND_CODE = "ENTITY_NOT_FOUND_SUMMARY"
ALREADY_RUNNING_CODE = "SUMMARY_ALREADY_RUNNING"
KEY_CODES = frozenset({"SUMMARY_CLOUD_KEY_MISSING", "SUMMARY_AUTH_FAILED"})
_RUNNING = "RUNNING"
_FAILED = "FAILED"


class SummaryPhase(StrEnum):
    IDLE = "idle"
    LOADING = "loading"
    NONE = "none"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class SummaryState:
    meeting_id: str = ""
    phase: SummaryPhase = SummaryPhase.IDLE
    button_label: str = BUTTON_TEXT
    can_generate: bool = False
    can_cancel: bool = False
    progress_text: str = ""
    progress_fraction: float | None = None  # None: not known yet (the view pulses)
    title: str = ""
    text: str = ""
    source_text: str = ""  # who made it, as the service worded it
    is_cloud: bool = False  # a "cloud" badge is shown while this is true
    stale_text: str = ""
    message: str = ""
    hint: str = ""
    needs_api_key: bool = False
    can_set_key: bool = False  # the configured provider is the cloud and may be used
    can_copy: bool = False
    filename: str = ""


class SummaryPresenter:
    def __init__(
        self,
        gateway: SummariesGateway,
        on_change: Callable[[SummaryState], None],
        on_confirm: Callable[[ConfirmationRequest], None],
    ) -> None:
        self._gateway = gateway
        self._on_change = on_change
        self._on_confirm = on_confirm
        self._state = SummaryState()
        self._generation = 0
        self._reset("")

    @property
    def state(self) -> SummaryState:
        return self._state

    def open(self, meeting_id: str, meeting_state: str) -> None:
        """Show the summary of a meeting; nothing is shown until it is DONE."""
        self._generation += 1
        self._reset(meeting_id)
        self._done = meeting_state == STATE_DONE
        if self._done:
            self._load()
        else:
            self._publish()

    def close(self) -> None:
        self._generation += 1
        self._reset("")
        self._publish()

    def request(self) -> None:
        """A press on the button: the only way a summary starts."""
        state = self._state
        row = self._default
        if not state.can_generate or row is None:
            return
        if row.cloud:
            self._on_confirm(ConfirmationRequest(self._meeting_id, row.id, row.label))
        else:
            self._start(row.id, cloud_confirmed=False)

    def send(self, request: ConfirmationRequest) -> None:
        """The confirmed cloud request; ignored when the user has moved to another meeting."""
        row = self._default
        if request.meeting_id != self._meeting_id or not self._state.can_generate or row is None:
            return
        if not row.cloud:
            return  # the default is no longer the cloud: there is nothing to confirm
        if row.id != request.provider_id:  # the choice changed: the user confirmed another one
            self._on_confirm(ConfirmationRequest(self._meeting_id, row.id, row.label))
            return
        self._start(request.provider_id, cloud_confirmed=True)

    def cancel(self) -> None:
        if not self._state.can_cancel:
            return
        self._cancelling = True
        self._publish()
        generation, meeting_id = self._generation, self._meeting_id

        def answered(result: Result[bool, ServiceError]) -> None:
            if generation != self._generation:
                return
            if isinstance(result, Err):
                self._cancelling = False
                self._error = result.error
                self._publish()
            elif not result.value:  # nothing was running any more
                self._refresh(finished=True)

        self._gateway.cancel_summary(meeting_id, answered)

    def on_service_available(self, available: bool) -> None:
        if not self._meeting_id or not self._done:
            return
        if available:
            self._generation += 1
            self._load()
        else:
            self._generation += 1
            self._load_error = ServiceError(SERVICE_NOT_RUNNING)
            self._publish()

    def on_processing_state_changed(self, meeting_id: str, state: str) -> None:
        if meeting_id != self._meeting_id:
            return
        self._generation += 1
        self._done = state == STATE_DONE
        if self._done:
            self._load()
        else:
            self._publish()

    def on_processing_finished(self, meeting_id: str, state: str) -> None:
        self.on_processing_state_changed(meeting_id, state)

    def on_speakers_changed(self, meeting_id: str) -> None:
        if meeting_id == self._meeting_id and self._done:
            self._refresh(finished=False)

    def on_summary_progress(self, meeting_id: str, step: int, total_steps: int) -> None:
        if meeting_id != self._meeting_id or not self._done:
            return
        self._in_progress = True
        self._step, self._total = step, total_steps
        self._publish()

    def on_summary_finished(self, meeting_id: str, state: str, error_code: str) -> None:  # noqa: ARG002
        if meeting_id == self._meeting_id and self._done:
            self._refresh(finished=True)

    # -- loading ---------------------------------------------------------------------------

    def _reset(self, meeting_id: str) -> None:
        self._meeting_id = meeting_id
        self._done = False
        self._loading = False
        self._providers: list[ProviderRow] = []
        self._info: SummaryInfo | None = None
        self._document: SummaryDocument | None = None
        self._load_error: ServiceError | None = None
        self._error: ServiceError | None = None
        self._reset_run()

    def _reset_run(self) -> None:
        """Forget what this app believes about a run; the service says what is really running."""
        self._requesting = False  # a press whose reply has not arrived yet
        self._in_progress = False  # a run known to be going (progress or an accepted start)
        self._cancelling = False
        self._step = 0
        self._total = 0

    @property
    def _default(self) -> ProviderRow | None:
        return next((row for row in self._providers if row.default), None)

    def _load(self) -> None:
        self._loading = True
        self._reset_run()
        self._load_error = None
        self._error = None
        self._document = None
        self._info = None
        self._publish()
        generation = self._generation

        def providers_loaded(result: Result[list[ProviderRow], ServiceError]) -> None:
            if generation != self._generation:
                return
            if isinstance(result, Err):
                self._fail_load(result.error)
                return
            self._providers = list(result.value)
            self._refresh(finished=False)

        self._gateway.providers(providers_loaded)

    def _fail_load(self, error: ServiceError) -> None:
        self._loading = False
        self._load_error = error
        self._publish()

    def _refresh(self, *, finished: bool) -> None:
        generation, meeting_id = self._generation, self._meeting_id

        def status_loaded(result: Result[SummaryInfo, ServiceError]) -> None:
            if generation != self._generation:
                return
            if finished:
                self._reset_run()
            if isinstance(result, Err):
                if self._info is None:
                    self._fail_load(result.error)
                else:
                    self._error = result.error
                    self._publish()
                return
            self._info = result.value
            self._error = None
            self._load_document(result.value)

        self._gateway.get_summary_status(meeting_id, status_loaded)

    def _load_document(self, info: SummaryInfo) -> None:
        document = self._document
        if not info.has_summary:
            self._document = None
        elif document is None or document.generated_at != info.generated_at:
            self._fetch_document()
            return
        self._loading = False
        self._publish()

    def _fetch_document(self) -> None:
        generation = self._generation

        def loaded(result: Result[SummaryDocument, ServiceError]) -> None:
            if generation != self._generation:
                return
            self._loading = False
            if isinstance(result, Err):
                self._document = None
                if result.error.code != NOT_FOUND_CODE:
                    self._error = result.error
            else:
                self._document = result.value
            self._publish()

        self._gateway.get_summary(self._meeting_id, loaded)

    # -- starting --------------------------------------------------------------------------

    def _start(self, provider_id: str, *, cloud_confirmed: bool) -> None:
        self._requesting = True
        self._error = None
        self._step = self._total = 0
        self._publish()
        generation = self._generation

        def started(result: Result[str, ServiceError]) -> None:
            if generation != self._generation:
                return
            self._requesting = False
            if not isinstance(result, Err):
                self._in_progress = True
                self._publish()
                return
            if result.error.code == ALREADY_RUNNING_CODE:
                self._refresh(finished=False)
                return
            self._error = result.error
            self._publish()

        self._gateway.summarize(
            self._meeting_id, provider_id, started, cloud_confirmed=cloud_confirmed
        )

    # -- the state shown ---------------------------------------------------------------------

    def _publish(self) -> None:
        self._state = self._build()
        self._on_change(self._state)

    def _failure(self) -> ServiceError | None:
        if self._error is not None:
            return self._error
        info = self._info
        if info is not None and info.state == _FAILED and info.failure_code:
            return ServiceError(info.failure_code)
        return None

    def _build(self) -> SummaryState:
        if not self._meeting_id or not self._done:
            return SummaryState(meeting_id=self._meeting_id)
        row = self._default
        label = BUTTON_TEXT if row is None else f"{BUTTON_TEXT}: {row.label}"
        if self._load_error is not None:
            message = message_for(self._load_error)
            return SummaryState(
                self._meeting_id,
                SummaryPhase.ERROR,
                label,
                message=message.text,
                hint=message.hint,
            )
        if self._loading:
            return SummaryState(self._meeting_id, SummaryPhase.LOADING, label)
        running = (
            self._requesting
            or self._in_progress
            or (self._info is not None and self._info.state == _RUNNING)
        )
        return self._loaded(label, row, running=running)

    def _loaded(self, label: str, row: ProviderRow | None, *, running: bool) -> SummaryState:
        info, document, failure = self._info, self._document, self._failure()
        step, total = (info.step, info.total_steps) if info is not None else (0, 0)
        step, total = (self._step, self._total) if self._total else (step, total)
        message, hint = "", ""
        if failure is not None:
            text = message_for(failure)
            message, hint = text.text, text.hint
        elif row is not None and not row.available:
            message = message_for(ServiceError(row.reason)).text
        if running:
            phase = SummaryPhase.RUNNING
        elif failure is not None:
            phase = SummaryPhase.FAILED
        else:
            phase = SummaryPhase.READY if document is not None else SummaryPhase.NONE
        return SummaryState(
            meeting_id=self._meeting_id,
            phase=phase,
            button_label=label,
            can_generate=not running and row is not None and row.available,
            can_cancel=running and not self._cancelling,
            progress_text=_progress_text(step, total, cancelling=self._cancelling)
            if running
            else "",
            progress_fraction=step / total if running and total > 0 else None,
            title=document.title if document else "",
            text=document.markdown if document else "",
            source_text=document.provider_label if document else "",
            is_cloud=document.cloud if document else False,
            stale_text=STALE_TEXT if info is not None and info.stale and document else "",
            message=message if not running else "",
            hint=hint if not running else "",
            needs_api_key=failure is not None and failure.code in KEY_CODES and not running,
            can_set_key=row is not None and row.cloud and row.available,
            can_copy=document is not None,
            filename=f"summary-{self._meeting_id}.md" if document else "",
        )


def _progress_text(step: int, total: int, *, cancelling: bool) -> str:
    if cancelling:
        return "Cancelling…"
    return f"Step {step} of {total}" if total > 0 else "Starting…"
