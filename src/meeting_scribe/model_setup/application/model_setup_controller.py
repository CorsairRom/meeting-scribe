"""Status, download and cancel of the speech models (spec install.md sections 6 and 7).

One download at a time, on its own thread; every blocking call (the keyring, the cache probe, the
child process) happens there or in a thread the caller chose, never on the D-Bus event loop. Return
values are ``Result``; failures of the download itself are not raised to a client: they arrive as
``finished(FAILED, code)`` and in the status. The token is read inside the job, handed to the
runner and dropped; it is never stored, logged or part of any status or event.
"""

import logging
import threading

from returns.result import Failure, Result, Success

from meeting_scribe.base.domain.errors import MeetingScribeError
from meeting_scribe.model_setup.domain.errors import (
    ModelsDownloadActiveError,
    ModelsDownloadCancelledError,
    ModelsDownloadFailedError,
)
from meeting_scribe.model_setup.domain.models import (
    NOT_CACHED,
    TOTAL_STEPS,
    CacheStatus,
    DownloadState,
    FailurePolicy,
    ModelsStatus,
    Outcome,
)
from meeting_scribe.model_setup.domain.ports import (
    CacheProbe,
    DownloadEvents,
    DownloadRunner,
    TokenSource,
)

logger = logging.getLogger(__name__)


class _Flight:
    """One cache probe in progress; the callers that arrive meanwhile wait for its answer."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.result = NOT_CACHED


class ModelSetupController:
    def __init__(
        self,
        tokens: TokenSource,
        probe: CacheProbe,
        runner: DownloadRunner,
        download_timeout_seconds: float,
    ) -> None:
        self._tokens = tokens
        self._probe = probe
        self._runner = runner
        self._timeout = download_timeout_seconds
        self._policy = FailurePolicy()
        self._lock = threading.Lock()
        self._observers: tuple[DownloadEvents, ...] = ()
        self._cache: CacheStatus | None = None  # remembered until a download ends
        self._active = False
        self._stopping = False  # shutdown began: no child may start any more
        self._started = False  # the job is announced: only then is there something to cancel
        self._flight: _Flight | None = None
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._step = 0
        self._total = 0
        self._failure_code = ""

    def subscribe(self, observer: DownloadEvents) -> None:
        self._observers = (*self._observers, observer)

    def unsubscribe(self, observer: DownloadEvents) -> None:
        self._observers = tuple(o for o in self._observers if o is not observer)

    @property
    def active(self) -> bool:
        return self._active

    def status(self) -> ModelsStatus:
        """Blocks when the cache is not known yet (one bounded offline check): call it off-loop."""
        with self._lock:
            active, cache = self._active, self._cache
        if cache is None and not active:
            cache = self._single_flight_probe()
        stored = self._tokens.token_stored()
        with self._lock:
            return self._status(cache or NOT_CACHED, stored)

    def download(self) -> Result[str, MeetingScribeError]:
        """``DOWNLOADING`` once the job started; ``READY`` when nothing is missing (nothing runs).

        The cache is checked afresh (a remembered answer may be stale), blocking and bounded: call
        it off-loop. A download never touches a cache that is already complete, so it cannot race
        a processing job that reads it.
        """
        with self._lock:
            if self._stopping:
                return Failure(ModelsDownloadCancelledError("The service is shutting down."))
            if self._active:
                return Failure(ModelsDownloadActiveError("A download is already running."))
            self._active = True
            self._started = False
            self._cancel = threading.Event()
            cancel = self._cancel
        cache = self._probe_and_remember()
        if cache.ready:
            with self._lock:
                self._active = False
            return Success("READY")
        with self._lock:
            if self._stopping:  # shutdown began during the check: start nothing
                self._active = False
                return Failure(ModelsDownloadCancelledError("The service is shutting down."))
            self._failure_code = ""
            self._step, self._total = 0, TOTAL_STEPS
            self._started = True
            self._thread = threading.Thread(
                target=self._job, args=(cancel,), name="model-download", daemon=True
            )
            thread = self._thread
        self._tell("active_changed")
        thread.start()
        return Success("DOWNLOADING")

    def cancel(self) -> bool:
        """True when a download was running; the outcome arrives as ``finished(CANCELLED, "")``.

        False until ``download`` has decided to run (it checks the cache first and has not replied
        yet): there is nothing to cancel then, and a true answer always ends as ``CANCELLED``.
        """
        with self._lock:
            if not (self._active and self._started):
                return False
            self._cancel.set()
            return True

    def shutdown(self, wait_seconds: float) -> bool:
        """Cancel a running download and wait for its thread, bounded; True when it ended."""
        with self._lock:
            self._stopping = True
            thread = self._thread
            if self._active:
                self._cancel.set()
        if thread is None:
            return True
        thread.join(wait_seconds)
        return not thread.is_alive()

    # -- the job thread --------------------------------------------------------------------

    def _job(self, cancel: threading.Event) -> None:
        state: Outcome = "FAILED"
        code = ModelsDownloadFailedError.error_code
        try:
            state, code = self._run(cancel)
        except Exception:  # a background thread: nobody would see it otherwise
            logger.exception("The model download crashed.")
        self._finish(state, code)

    def _run(self, cancel: threading.Event) -> tuple[Outcome, str]:
        if cancel.is_set():  # shutdown or a cancel arrived before the child could start
            return self._cancelled()
        token = self._tokens.read_token()
        if isinstance(token, Failure):
            return self._failed(self._policy.code_for(token.failure()))
        ran = self._runner.run(token.unwrap(), self._timeout, self._on_progress, cancel)
        return self._after_run(ran, cancel)

    def _after_run(
        self, ran: Result[None, MeetingScribeError], cancel: threading.Event
    ) -> tuple[Outcome, str]:
        if isinstance(ran, Failure):
            error = ran.failure()
            if isinstance(error, ModelsDownloadCancelledError):
                return self._cancelled()
            return self._failed(self._policy.code_for(error))
        cache = self._probe_and_remember()  # nothing partial is ever reported as ready
        if cancel.is_set():  # a true CancelDownload always ends as CANCELLED, even if it lost
            return self._cancelled()
        if cache.ready:
            return "READY", ""
        return "FAILED", ModelsDownloadFailedError.error_code

    def _cancelled(self) -> tuple[Outcome, str]:
        self._forget_cache()
        return "CANCELLED", ""

    def _failed(self, code: str) -> tuple[Outcome, str]:
        self._forget_cache()
        return "FAILED", code

    def _on_progress(self, model: str, step: int, total: int) -> None:
        with self._lock:
            self._step, self._total = step, total
        self._tell("progress", model, step, total)

    def _finish(self, state: Outcome, code: str) -> None:
        with self._lock:
            self._failure_code = code if state == "FAILED" else ""
            self._step = self._total = 0
            self._active = False
            self._thread = None
        self._tell("active_changed")
        self._tell("finished", state, code)  # last: the result is recorded and the slot is free

    # -- helpers ---------------------------------------------------------------------------

    def _probe_and_remember(self) -> CacheStatus:
        try:
            cache = self._probe.check()
        except Exception:  # the contract says never; a failed probe means "not cached"
            logger.warning("The model cache check failed.", exc_info=True)
            cache = NOT_CACHED
        with self._lock:
            self._cache = cache
        return cache

    def _single_flight_probe(self) -> CacheStatus:
        """The remembered cache, probing at most once at a time however many callers ask."""
        with self._lock:
            if self._cache is not None:
                return self._cache
            flight = self._flight
            leader = flight is None
            if flight is None:
                flight = self._flight = _Flight()
        if not leader:
            flight.done.wait()
            return flight.result
        try:
            flight.result = self._probe_and_remember()
        finally:
            with self._lock:
                self._flight = None
            flight.done.set()
        return flight.result

    def _forget_cache(self) -> None:
        with self._lock:
            self._cache = None

    def _status(self, cache: CacheStatus, stored: bool) -> ModelsStatus:
        state: DownloadState
        if self._active:
            state = "DOWNLOADING"
        elif self._failure_code:
            state = "FAILED"
        else:
            state = "READY" if cache.ready else "PENDING"
        return ModelsStatus(
            state=state,
            whisper_cached=cache.whisper_cached,
            pipeline_cached=cache.pipeline_cached,
            token_stored=stored,
            step=self._step,
            total_steps=self._total,
            failure_code=self._failure_code,
        )

    def _tell(self, event: str, *args: object) -> None:
        for observer in self._observers:
            try:
                getattr(observer, event)(*args)
            except Exception:
                logger.warning("A download observer failed.", exc_info=True)
