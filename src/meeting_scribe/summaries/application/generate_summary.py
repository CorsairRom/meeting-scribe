"""``GenerateSummary``: one pass or map-reduce, cancel, progress, validated output (spec 3, 4)."""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict
from returns.result import Failure, Result, Success

from meeting_scribe.summaries.domain.chunker import (
    SEPARATOR,
    chunk_transcript,
    fits,
    format_line,
    pack_texts,
)
from meeting_scribe.summaries.domain.errors import (
    SummaryCancelledError,
    SummaryContextTooLargeError,
    SummaryError,
    SummaryNoSpeechError,
    SummaryOutputInvalidError,
    SummaryOutputLimitReachedError,
    SummaryTimeoutError,
)
from meeting_scribe.summaries.domain.fingerprint import input_fingerprint
from meeting_scribe.summaries.domain.headings import (
    EnglishHeadingCatalog,
    HeadingCatalog,
    Headings,
)
from meeting_scribe.summaries.domain.ports import (
    CancelSignal,
    CloudKeyStore,
    Completion,
    Message,
    ModelReleaser,
    SummaryProvider,
)
from meeting_scribe.summaries.domain.prompts import PROMPT_VERSION, PromptBuilder
from meeting_scribe.summaries.domain.renderer import SummaryText, parse_body, render_summary
from meeting_scribe.summaries.domain.request import CompletionLimits, SummaryLine, SummaryRequest
from meeting_scribe.summaries.domain.session import new_session_id

__all__ = [
    "CloudKeyStore",
    "CompletionLimits",
    "GenerateSummary",
    "GeneratedSummary",
    "SummaryCancelledError",
    "SummaryError",
    "SummaryLine",
    "SummaryNoSpeechError",
    "SummaryRequest",
    "input_fingerprint",
]

MIN_CALL_SECONDS = 0.01
"""Smallest time a provider call is given; with less left, the run times out without calling."""

ProgressCallback = Callable[[int, int], None]


class GeneratedSummary(BaseModel):
    """The finished summary and what the caller stores next to it."""

    model_config = ConfigDict(frozen=True)

    text: SummaryText
    chunks: int
    input_fingerprint: str
    prompt_version: int


@dataclass
class _Run:
    """State of one summary: the request, its prompts, the clock and the step counter."""

    request: SummaryRequest
    headings: Headings
    cancel: CancelSignal
    on_progress: ProgressCallback | None
    deadline: float | None
    stop: threading.Event = field(default_factory=threading.Event)
    session_id: str = field(default_factory=new_session_id)
    prompts: PromptBuilder = field(init=False)
    step: int = 0
    total: int = 1

    def __post_init__(self) -> None:
        self.prompts = PromptBuilder(self.request, self.headings)

    def advance(self) -> None:
        self.step += 1
        if self.on_progress is not None:
            self.on_progress(self.step, self.total)


class GenerateSummary:
    def __init__(
        self,
        provider: SummaryProvider,
        releaser: ModelReleaser,
        clock: Callable[[], float] = time.monotonic,
        catalog: HeadingCatalog | None = None,
    ) -> None:
        self._provider = provider
        self._releaser = releaser
        self._clock = clock
        self._catalog = catalog or EnglishHeadingCatalog()

    def run(
        self,
        request: SummaryRequest,
        cancel: CancelSignal,
        on_progress: ProgressCallback | None = None,
        total_timeout_seconds: float | None = None,
    ) -> Result[GeneratedSummary, SummaryError]:
        """Summarize; never raises. The model is released however the run ends.

        ``on_progress(step, total)`` follows each step; ``total`` may grow while merge levels are
        added. ``total_timeout_seconds`` bounds the whole run (``SUMMARY_TIMEOUT``).

        Two time sources, on purpose: the injected clock (monotonic by default) is the authority
        for the deadline and the per-call time left; a real-time ``threading.Timer`` only wakes a
        provider call that is stalled, because only real time can interrupt a blocked call. With
        a fake clock the timer never fires in a test, so the clock alone decides.
        """
        deadline = None if total_timeout_seconds is None else self._clock() + total_timeout_seconds
        run = _Run(
            request, self._catalog.headings_for(request.language), cancel, on_progress, deadline
        )
        timer = None
        if total_timeout_seconds is not None:
            timer = threading.Timer(max(total_timeout_seconds, 0.0), run.stop.set)
            timer.daemon = True
            timer.start()
        try:
            return self._run(run)
        finally:
            if timer is not None:
                timer.cancel()
            self._releaser.release()

    def _run(self, run: _Run) -> Result[GeneratedSummary, SummaryError]:
        request = run.request
        if not request.lines:
            return Failure(SummaryNoSpeechError("There is nothing to summarize."))
        usable = request.usable_input_tokens
        if usable <= 0:
            return Failure(_too_large("The context leaves no room for the transcript."))
        transcript = SEPARATOR.join(format_line(item) for item in request.lines)
        if fits(transcript, usable):
            chunks = 1
            final = self._ask(run, run.prompts.single_pass(transcript))
        else:
            parts = chunk_transcript(request.lines, usable)
            chunks = len(parts)
            run.total = chunks + 1
            reduced = self._map_reduce(run, parts, usable)
            if isinstance(reduced, Failure):
                return reduced
            final = self._ask(run, run.prompts.reduce(reduced.unwrap()))
        if isinstance(final, Failure):
            return final
        text = self._validated(run, final.unwrap())
        if isinstance(text, Failure):
            return text
        return Success(
            GeneratedSummary(
                text=text.unwrap(),
                chunks=chunks,
                input_fingerprint=input_fingerprint(request.lines),
                prompt_version=PROMPT_VERSION,
            )
        )

    def _map_reduce(
        self, run: _Run, parts: tuple[str, ...], usable: int
    ) -> Result[list[str], SummaryError]:
        notes: list[str] = []
        for number, part in enumerate(parts, start=1):
            answer = self._ask(run, run.prompts.map_notes(part, number, len(parts)))
            if isinstance(answer, Failure):
                return answer
            notes.append(answer.unwrap())
        while not fits(run.prompts.notes_payload(notes), usable):
            batches = pack_texts(notes, usable, run.prompts.notes_payload)
            if len(batches) >= len(notes):
                return Failure(_too_large("The partial notes cannot be merged within the context."))
            run.total += sum(1 for batch in batches if len(batch) > 1)
            merged: list[str] = []
            for batch in batches:
                if len(batch) == 1:
                    merged.append(batch[0])
                    continue
                answer = self._ask(run, run.prompts.merge_notes(batch))
                if isinstance(answer, Failure):
                    return answer
                merged.append(answer.unwrap())
            notes = merged
        return Success(notes)

    def _validated(self, run: _Run, output: str) -> Result[SummaryText, SummaryError]:
        headings = run.headings
        body = parse_body(output, headings)
        if isinstance(body, Failure):
            repaired = self._request(run, run.prompts.repair(output))
            if isinstance(repaired, Failure):
                return repaired
            body = parse_body(repaired.unwrap(), headings)
            if isinstance(body, Failure):
                return body
        return Success(render_summary(run.request, body.unwrap(), headings))

    def _ask(self, run: _Run, messages: list[Message]) -> Result[str, SummaryError]:
        """One planned step: the request, then the progress report."""
        answer = self._request(run, messages)
        if isinstance(answer, Success):
            run.advance()
        return answer

    def _request(self, run: _Run, messages: list[Message]) -> Result[str, SummaryError]:
        if (stopped := self._stopped(run)) is not None:
            return Failure(stopped)
        remaining = None
        if run.deadline is not None:
            remaining = run.deadline - self._clock()
            if remaining <= 0:
                return Failure(_timeout())
            remaining = max(remaining, MIN_CALL_SECONDS)
        completed = self._provider.complete(
            messages,
            run.request.limits,
            _Either(run.cancel, run.stop),
            remaining,
            session_id=run.session_id,
        )
        if (stopped := self._stopped(run)) is not None:
            return Failure(stopped)
        if isinstance(completed, Failure):
            return completed
        return _accepted(completed.unwrap())

    def _stopped(self, run: _Run) -> SummaryError | None:
        """The reason to stop now. A user cancel wins over an expired deadline."""
        if run.cancel.is_set():
            return SummaryCancelledError("The summary was cancelled. Press Generate again.")
        if run.stop.is_set() or (run.deadline is not None and self._clock() >= run.deadline):
            return _timeout()
        return None


class _Either:
    """A cancel signal that is set when the caller cancels or the total deadline passes."""

    _SLICE = 0.05

    def __init__(self, caller: CancelSignal, deadline: threading.Event) -> None:
        self._caller = caller
        self._deadline = deadline

    def is_set(self) -> bool:
        return self._caller.is_set() or self._deadline.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        end = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            left = None if end is None else end - time.monotonic()
            if left is not None and left <= 0:
                return False
            self._deadline.wait(self._SLICE if left is None else min(self._SLICE, left))
        return True


def _accepted(completion: Completion) -> Result[str, SummaryError]:
    if completion.finish_reason == "length":
        return Failure(
            SummaryOutputLimitReachedError(
                "The model used its whole output limit before finishing the summary. "
                "Raise SUMMARIES_MAX_OUTPUT_TOKENS or choose another model."
            )
        )
    if not completion.text.strip():
        return Failure(SummaryOutputInvalidError("The model returned an empty answer."))
    return Success(completion.text)


def _timeout() -> SummaryTimeoutError:
    return SummaryTimeoutError("The summary took too long. Retry or raise the timeout.")


def _too_large(reason: str) -> SummaryContextTooLargeError:
    return SummaryContextTooLargeError(f"{reason} Raise the context and the matching setting.")
