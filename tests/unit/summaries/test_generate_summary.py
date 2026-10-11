import time
from collections.abc import Callable

from returns.result import Failure, Success

from meeting_scribe.summaries.application.generate_summary import GenerateSummary
from meeting_scribe.summaries.domain.chunker import estimate_tokens, format_line
from meeting_scribe.summaries.domain.errors import (
    SummaryCancelledError,
    SummaryContextTooLargeError,
    SummaryNoSpeechError,
    SummaryOutputInvalidError,
    SummaryProviderUnavailableError,
    SummaryTimeoutError,
)
from meeting_scribe.summaries.domain.fingerprint import input_fingerprint
from meeting_scribe.summaries.domain.ports import Completion
from meeting_scribe.summaries.domain.prompts import PROMPT_VERSION
from meeting_scribe.summaries.domain.request import SummaryRequest
from tests.unit.summaries.factories import line, request
from tests.unit.summaries.fakes import (
    VALID_OUTPUT,
    FakeCancel,
    FakeClock,
    FakeProvider,
    FakeReleaser,
    by_prompt_kind,
)

CONTEXT = 1200
MAX_OUTPUT = 200


def _long_request(count: int = 30) -> SummaryRequest:
    lines = tuple(
        line(
            "Person A" if n % 2 else "Person B", float(n * 10), f"synthetic utterance {n:03d} " * 4
        )
        for n in range(count)
    )
    return request(lines=lines, context_tokens=CONTEXT, max_output_tokens=MAX_OUTPUT)


def _use_case(
    provider: FakeProvider,
    releaser: FakeReleaser | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> GenerateSummary:
    return GenerateSummary(provider, releaser or FakeReleaser(), clock)


def _progress() -> tuple[list[tuple[int, int]], Callable[[int, int], None]]:
    seen: list[tuple[int, int]] = []
    return seen, lambda step, total: seen.append((step, total))


def test_single_pass_when_it_fits() -> None:
    provider = FakeProvider()
    seen, on_progress = _progress()
    req = request()

    result = _use_case(provider).run(req, FakeCancel(), on_progress)

    generated = result.unwrap()
    assert len(provider.requests) == 1
    assert seen == [(1, 1)]
    assert generated.text.title == "Synthetic title"
    assert generated.text.markdown.startswith("# Synthetic title - 2031-03-04 (14:05, ~45 min)")
    assert generated.chunks == 1
    assert generated.prompt_version == PROMPT_VERSION
    assert generated.input_fingerprint == input_fingerprint(req.lines)


def test_map_reduce_steps() -> None:
    req = _long_request()
    provider = FakeProvider()
    seen, on_progress = _progress()

    generated = _use_case(provider).run(req, FakeCancel(), on_progress).unwrap()

    maps = [r for r in provider.requests if r[0].content.startswith("You extract")]
    assert generated.chunks == len(maps) > 1
    assert len(provider.requests) == len(maps) + 1
    assert provider.requests[-1][0].content.startswith("You write meeting minutes")
    for messages in provider.requests:
        assert estimate_tokens("\n".join(m.content for m in messages)) <= CONTEXT - MAX_OUTPUT
    sent = "\n".join(r[-1].content for r in maps)
    for item in req.lines:
        assert sent.count(format_line(item)) == 1
    assert seen[-1] == (len(provider.requests), len(provider.requests))
    assert [step for step, _ in seen] == list(range(1, len(seen) + 1))


def test_notes_are_merged_in_batches_until_they_fit() -> None:
    big_notes = "- " + "n" * 500
    provider = FakeProvider(by_prompt_kind(notes=big_notes))
    seen, on_progress = _progress()

    result = _use_case(provider).run(_long_request(), FakeCancel(), on_progress)

    assert isinstance(result, Success)
    merges = [r for r in provider.requests if r[0].content.startswith("You merge partial")]
    assert merges
    assert seen[-1][0] == seen[-1][1] == len(provider.requests)
    for messages in provider.requests:
        assert estimate_tokens("\n".join(m.content for m in messages)) <= CONTEXT - MAX_OUTPUT


def test_notes_that_cannot_shrink_fail_as_too_large() -> None:
    provider = FakeProvider(by_prompt_kind(notes="- " + "n" * 1300))

    result = _use_case(provider).run(_long_request(), FakeCancel())

    assert isinstance(result.failure(), SummaryContextTooLargeError)


def test_a_budget_without_room_fails_before_any_request() -> None:
    provider = FakeProvider()
    req = request(context_tokens=700, max_output_tokens=200)

    result = _use_case(provider).run(req, FakeCancel())

    assert isinstance(result.failure(), SummaryContextTooLargeError)
    assert provider.requests == []


def test_no_speech_calls_no_provider() -> None:
    provider = FakeProvider()

    result = _use_case(provider).run(request(lines=()), FakeCancel())

    assert isinstance(result.failure(), SummaryNoSpeechError)
    assert provider.requests == []


def test_cancel_before_the_first_request() -> None:
    provider = FakeProvider()
    cancel = FakeCancel()
    cancel.set()

    result = _use_case(provider).run(_long_request(), cancel)

    assert isinstance(result.failure(), SummaryCancelledError)
    assert provider.requests == []


def test_cancel_between_and_during_requests() -> None:
    cancel = FakeCancel()
    provider = FakeProvider()
    provider.on_call = lambda count: cancel.set() if count == 2 else None

    between = _use_case(provider).run(_long_request(), cancel)

    assert isinstance(between.failure(), SummaryCancelledError)
    assert len(provider.requests) == 2

    during = FakeProvider(lambda _m: SummaryCancelledError("cancelled"))
    result = _use_case(during).run(_long_request(), FakeCancel())
    assert isinstance(result.failure(), SummaryCancelledError)
    assert len(during.requests) == 1


def test_cancel_after_the_last_answer_still_cancels() -> None:
    cancel = FakeCancel()
    provider = FakeProvider()
    provider.on_call = lambda _count: cancel.set()

    result = _use_case(provider).run(request(), cancel)

    assert isinstance(result.failure(), SummaryCancelledError)


def test_invalid_output_gets_one_repair_request() -> None:
    answers = iter(["just prose, no sections", VALID_OUTPUT])
    provider = FakeProvider(lambda _m: next(answers))

    result = _use_case(provider).run(request(), FakeCancel())

    assert isinstance(result, Success)
    assert len(provider.requests) == 2
    repair = provider.requests[1][-1].content
    assert "<draft>" in repair
    assert "just prose, no sections" in repair


def test_output_still_invalid_after_the_repair_fails() -> None:
    provider = FakeProvider(lambda _m: "still no sections")

    result = _use_case(provider).run(request(), FakeCancel())

    assert isinstance(result.failure(), SummaryOutputInvalidError)
    assert len(provider.requests) == 2


def test_empty_output_fails_without_repair() -> None:
    provider = FakeProvider(lambda _m: "  \n ")

    result = _use_case(provider).run(request(), FakeCancel())

    assert isinstance(result.failure(), SummaryOutputInvalidError)
    assert len(provider.requests) == 1


def test_output_cut_at_the_token_limit_is_never_accepted() -> None:
    provider = FakeProvider(lambda _m: Completion(text=VALID_OUTPUT, finish_reason="length"))

    result = _use_case(provider).run(request(), FakeCancel())

    assert isinstance(result.failure(), SummaryContextTooLargeError)


def test_a_provider_failure_stops_the_summary() -> None:
    provider = FakeProvider(lambda _m: SummaryProviderUnavailableError("down"))

    result = _use_case(provider).run(_long_request(), FakeCancel())

    assert isinstance(result.failure(), SummaryProviderUnavailableError)
    assert len(provider.requests) == 1


def test_the_model_is_released_however_the_summary_ends() -> None:
    cancelled = FakeCancel()
    cancelled.set()
    cases = [
        (FakeProvider(), FakeCancel()),
        (FakeProvider(lambda _m: SummaryProviderUnavailableError("down")), FakeCancel()),
        (FakeProvider(), cancelled),
    ]
    for provider, cancel in cases:
        releaser = FakeReleaser()
        _use_case(provider, releaser).run(request(), cancel)
        assert releaser.calls == 1


def test_total_timeout_fails_with_summary_timeout() -> None:
    clock = FakeClock()
    provider = FakeProvider()
    provider.on_call = lambda _count: setattr(clock, "now", clock.now + 100.0)

    result = _use_case(provider, clock=clock).run(
        _long_request(), FakeCancel(), total_timeout_seconds=150.0
    )

    assert isinstance(result.failure(), SummaryTimeoutError)
    assert len(provider.requests) == 2


def test_cloud_request_payload_has_no_identifiers() -> None:
    provider = FakeProvider()
    req = request(lines=(line("Alex", 0.0, "we ship on friday."),))

    _use_case(provider).run(req, FakeCancel()).unwrap()

    sent = "\n".join(m.content for m in provider.requests[0])
    assert "Alex" in sent
    assert "we ship on friday." in sent
    assert "2031-03-04" in sent
    assert "45 minutes" in sent
    for forbidden in (req.provider_label, req.limits.model, "summary.md", "meeting.json", "/home"):
        assert forbidden not in sent


def test_failures_are_results_not_exceptions() -> None:
    result = _use_case(FakeProvider()).run(request(lines=()), FakeCancel())

    assert isinstance(result, Failure)
