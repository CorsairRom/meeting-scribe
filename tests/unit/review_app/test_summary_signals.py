"""Unpacked Summaries1 signal arguments reach the listener; a malformed signal is dropped."""

from meeting_scribe_review.core.signals import dispatch_summary_signal


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []

    def on_summary_progress(self, meeting_id: str, step: int, total_steps: int) -> None:
        self.events.append(("progress", meeting_id, step, total_steps))

    def on_summary_finished(self, meeting_id: str, state: str, error_code: str) -> None:
        self.events.append(("finished", meeting_id, state, error_code))


def test_each_signal_calls_its_listener_method() -> None:
    rec = _Recorder()
    assert dispatch_summary_signal(rec, "SummaryProgress", ("m1", 1, 3))
    assert dispatch_summary_signal(rec, "SummaryFinished", ("m1", "FAILED", "SUMMARY_TIMEOUT"))
    assert rec.events == [
        ("progress", "m1", 1, 3),
        ("finished", "m1", "FAILED", "SUMMARY_TIMEOUT"),
    ]


def test_malformed_and_unknown_signals_are_dropped() -> None:
    rec = _Recorder()
    assert not dispatch_summary_signal(rec, "SummaryProgress", ("m1", 1))
    assert not dispatch_summary_signal(rec, "SummaryProgress", ("m1", True, 3))
    assert not dispatch_summary_signal(rec, "SummaryFinished", ("m1", "DONE", 0))
    assert not dispatch_summary_signal(rec, "SummaryFinished", "abc")
    assert not dispatch_summary_signal(rec, "Other", ("m1",))
    assert rec.events == []
