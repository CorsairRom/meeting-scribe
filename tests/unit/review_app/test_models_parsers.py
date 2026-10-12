"""Parsers and signals of ``Models1``: plain values to models; malformed ones are dropped."""

from meeting_scribe_review.core.errors import INVALID_REPLY, ServiceError
from meeting_scribe_review.core.models import ModelsStatus
from meeting_scribe_review.core.parsers import parse_download_reply, parse_models_status
from meeting_scribe_review.core.result import Err, Ok
from meeting_scribe_review.core.signals import dispatch_models_signal

STATUS = {
    "state": "DOWNLOADING",
    "whisper_cached": False,
    "pipeline_cached": True,
    "token_stored": True,
    "step": 2,
    "total_steps": 2,
    "failure_code": "",
}
INVALID = Err(ServiceError(INVALID_REPLY))


def test_parse_models_status_reads_every_member() -> None:
    assert parse_models_status(STATUS) == Ok(
        ModelsStatus("DOWNLOADING", False, True, True, 2, 2, "")
    )


def test_parse_models_status_rejects_a_missing_or_mistyped_member() -> None:
    assert parse_models_status({k: v for k, v in STATUS.items() if k != "step"}) == INVALID
    assert parse_models_status({**STATUS, "step": True}) == INVALID
    assert parse_models_status({**STATUS, "token_stored": "yes"}) == INVALID
    assert parse_models_status({**STATUS, "failure_code": 3}) == INVALID
    assert parse_models_status(["not", "a", "mapping"]) == INVALID


def test_parse_download_reply_is_the_state_text() -> None:
    assert parse_download_reply("DOWNLOADING") == Ok("DOWNLOADING")
    assert parse_download_reply("READY") == Ok("READY")
    assert parse_download_reply(7) == INVALID


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []

    def on_service_available(self, available: bool) -> None:
        self.events.append(("available", available))

    def on_models_progress(self, model: str, step: int, total_steps: int) -> None:
        self.events.append(("progress", model, step, total_steps))

    def on_models_finished(self, state: str, error_code: str) -> None:
        self.events.append(("finished", state, error_code))


def test_each_signal_calls_its_listener_method() -> None:
    rec = _Recorder()
    assert dispatch_models_signal(rec, "ModelsProgress", ("pipeline", 1, 2))
    assert dispatch_models_signal(rec, "ModelsFinished", ("FAILED", "MODEL_UNAVAILABLE"))
    assert rec.events == [
        ("progress", "pipeline", 1, 2),
        ("finished", "FAILED", "MODEL_UNAVAILABLE"),
    ]


def test_malformed_and_unknown_signals_are_dropped() -> None:
    rec = _Recorder()
    assert not dispatch_models_signal(rec, "ModelsProgress", ("pipeline", 1))
    assert not dispatch_models_signal(rec, "ModelsProgress", ("pipeline", True, 2))
    assert not dispatch_models_signal(rec, "ModelsFinished", ("READY", 0))
    assert not dispatch_models_signal(rec, "ModelsFinished", "ab")
    assert not dispatch_models_signal(rec, "Other", ("x",))
    assert rec.events == []
