"""Unpacked signal arguments reach the listener; a malformed signal is dropped (AC-12)."""

from meeting_scribe_review.core.signals import dispatch_signal


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []

    def on_service_available(self, available: bool) -> None:
        self.events.append(("available", available))

    def on_processing_state_changed(self, meeting_id: str, state: str) -> None:
        self.events.append(("state", meeting_id, state))

    def on_processing_finished(self, meeting_id: str, state: str, speaker_count: int) -> None:
        self.events.append(("finished", meeting_id, state, speaker_count))

    def on_speakers_changed(self, meeting_id: str) -> None:
        self.events.append(("speakers", meeting_id))


def test_each_signal_calls_its_listener_method() -> None:
    rec = _Recorder()
    assert dispatch_signal(rec, "ProcessingStateChanged", ("m1", "DIARIZING"))
    assert dispatch_signal(rec, "ProcessingFinished", ("m1", "DONE", 3))
    assert dispatch_signal(rec, "SpeakersChanged", ("m1",))
    assert rec.events == [
        ("state", "m1", "DIARIZING"),
        ("finished", "m1", "DONE", 3),
        ("speakers", "m1"),
    ]


def test_a_signal_with_the_wrong_shape_is_dropped() -> None:
    rec = _Recorder()
    assert not dispatch_signal(rec, "ProcessingStateChanged", ("m1",))
    assert not dispatch_signal(rec, "ProcessingFinished", ("m1", "DONE", "3"))
    assert not dispatch_signal(rec, "ProcessingFinished", ("m1", "DONE", True))
    assert not dispatch_signal(rec, "SpeakersChanged", (7,))
    assert not dispatch_signal(rec, "SpeakersChanged", "m1")
    assert rec.events == []


def test_an_unknown_signal_is_dropped() -> None:
    rec = _Recorder()
    assert not dispatch_signal(rec, "SomethingElse", ("m1",))
    assert rec.events == []
