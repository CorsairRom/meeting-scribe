"""The summary observer fan-out and the progress throttle."""

import logging

import pytest

from meeting_scribe.meetings.application.summary_events import ProgressThrottle, SummaryEventHub
from tests.unit.meetings.fakes import RecordingSummaryEvents


class Broken:
    def progress(self, *_args: object) -> None:
        raise RuntimeError("boom")

    def finished(self, *_args: object) -> None:
        raise RuntimeError("boom")

    def active_changed(self) -> None:
        raise RuntimeError("boom")


def test_every_subscriber_hears_every_event_even_after_a_broken_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hub = SummaryEventHub()
    heard = RecordingSummaryEvents()
    hub.subscribe(Broken())  # type: ignore[bad-argument-type]  # a deliberately loose double
    hub.subscribe(heard)

    with caplog.at_level(logging.WARNING):
        hub.progress("m1", 1, 3)
        hub.finished("m1", "DONE", "")
        hub.active_changed()

    assert heard.log == [("progress", "m1", "1", "3"), ("finished", "m1", "DONE", ""), ("active",)]
    assert len(caplog.records) == 3


def test_unsubscribe_stops_the_events() -> None:
    hub = SummaryEventHub()
    heard = RecordingSummaryEvents()
    hub.subscribe(heard)
    hub.unsubscribe(heard)

    hub.active_changed()

    assert heard.log == []


def test_throttle_allows_one_progress_per_interval_and_always_the_last_step() -> None:
    now = [100.0]
    throttle = ProgressThrottle(1.0, lambda: now[0])

    assert throttle.allow(1, 4) is True
    now[0] += 0.4
    assert throttle.allow(2, 4) is False
    now[0] += 0.7
    assert throttle.allow(3, 4) is True
    now[0] += 0.1
    assert throttle.allow(4, 4) is True  # the last step is never dropped


def test_throttle_starts_afresh_for_the_next_summary() -> None:
    now = [100.0]
    throttle = ProgressThrottle(1.0, lambda: now[0])
    assert throttle.allow(1, 4) is True

    throttle.reset()

    assert throttle.allow(1, 4) is True
