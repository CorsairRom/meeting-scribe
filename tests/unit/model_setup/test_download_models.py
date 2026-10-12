"""``ModelSetupController``: status, download, cancel and the token rules (AC-17, AC-18)."""

import threading

import pytest
from returns.result import Failure, Success

from meeting_scribe.model_setup.application.model_setup_controller import ModelSetupController
from meeting_scribe.model_setup.domain.errors import (
    ModelsDownloadActiveError,
    ModelsDownloadFailedError,
    ModelsDownloadReportedError,
    ModelsDownloadTimeoutError,
)
from meeting_scribe.model_setup.domain.models import CacheStatus
from tests.unit.model_setup.fakes import (
    SENTINEL,
    FakeCacheProbe,
    FakeRunner,
    FakeTokenSource,
    RecordingEvents,
    wait_until,
)

NONE = CacheStatus(False, False)
BOTH = CacheStatus(True, True)
TIMEOUT = 1234.0


def _controller(
    runner: FakeRunner | None = None,
    tokens: FakeTokenSource | None = None,
    probe: FakeCacheProbe | None = None,
) -> tuple[ModelSetupController, RecordingEvents, FakeRunner, FakeTokenSource, FakeCacheProbe]:
    runner = runner or FakeRunner()
    tokens = tokens or FakeTokenSource()
    probe = probe or FakeCacheProbe(NONE, BOTH)
    controller = ModelSetupController(tokens, probe, runner, download_timeout_seconds=TIMEOUT)
    events = RecordingEvents()
    controller.subscribe(events)
    return controller, events, runner, tokens, probe


def test_status_is_pending_when_the_models_are_not_cached() -> None:
    controller, *_ = _controller(probe=FakeCacheProbe(NONE))

    status = controller.status()

    assert status.state == "PENDING"
    assert (status.whisper_cached, status.pipeline_cached) == (False, False)
    assert (status.step, status.total_steps, status.failure_code) == (0, 0, "")


def test_status_is_ready_when_both_are_cached() -> None:
    controller, *_ = _controller(probe=FakeCacheProbe(BOTH))

    assert controller.status().state == "READY"


def test_status_says_whether_a_token_is_stored_without_reading_it() -> None:
    controller, _, _, tokens, _ = _controller(tokens=FakeTokenSource(SENTINEL))

    assert controller.status().token_stored is True
    assert tokens.reads == 0


def test_the_cache_is_probed_once_and_remembered() -> None:
    controller, _, _, _, probe = _controller(probe=FakeCacheProbe(NONE))

    controller.status()
    controller.status()

    assert probe.calls == 1


def test_download_replies_at_once_then_reports_progress_and_the_end() -> None:
    gate = threading.Event()
    controller, events, runner, _, _ = _controller(FakeRunner(gate=gate))

    reply = controller.download()

    assert reply == Success("DOWNLOADING")
    assert runner.started.wait(5)
    assert controller.status().state == "DOWNLOADING"
    assert controller.active
    gate.set()
    events.wait_finished()
    assert ("progress", ("pipeline", 1, 2)) in events.events
    assert ("progress", ("whisper", 2, 2)) in events.events
    assert events.events[-1] == ("finished", ("READY", ""))
    assert not controller.active
    assert controller.status().state == "READY"


def test_the_status_during_a_download_carries_the_step() -> None:
    gate = threading.Event()
    controller, _, runner, _, _ = _controller(FakeRunner(gate=gate))
    controller.download()
    assert runner.started.wait(5)

    wait_until(lambda: controller.status().step == 2)
    status = controller.status()

    assert (status.state, status.step, status.total_steps) == ("DOWNLOADING", 2, 2)
    gate.set()
    controller.shutdown(5)


def test_a_second_download_while_one_runs_fails_with_downloads_active() -> None:
    gate = threading.Event()
    controller, _, runner, _, _ = _controller(FakeRunner(gate=gate))
    controller.download()
    assert runner.started.wait(5)

    second = controller.download()

    assert isinstance(second, Failure)
    assert isinstance(second.failure(), ModelsDownloadActiveError)
    assert second.failure().error_code == "MODELS_DOWNLOAD_ACTIVE"
    gate.set()
    controller.shutdown(5)


def test_cancel_stops_the_download_and_reports_cancelled() -> None:
    gate = threading.Event()
    controller, events, runner, _, _ = _controller(
        FakeRunner(gate=gate), probe=FakeCacheProbe(NONE)
    )
    controller.download()
    assert runner.started.wait(5)

    assert controller.cancel() is True

    events.wait_finished()
    assert events.events[-1] == ("finished", ("CANCELLED", ""))
    assert controller.status().state == "PENDING"  # the cache is still not complete
    assert controller.status().failure_code == ""


def test_cancel_when_nothing_runs_is_false_and_not_an_error() -> None:
    controller, *_ = _controller()

    assert controller.cancel() is False


def test_download_checks_the_cache_afresh_instead_of_trusting_a_remembered_status() -> None:
    controller, events, runner, _, probe = _controller(probe=FakeCacheProbe(BOTH, NONE, BOTH))
    assert controller.status().state == "READY"  # remembered: both cached

    controller.download()  # the cache was emptied meanwhile: the fresh check sees it
    events.wait_finished()

    assert probe.calls == 3
    assert len(runner.runs) == 1


def test_download_when_both_models_are_cached_starts_nothing() -> None:
    controller, events, runner, tokens, _ = _controller(probe=FakeCacheProbe(BOTH))

    reply = controller.download()

    assert reply == Success("READY")
    assert runner.runs == []
    assert tokens.reads == 0
    assert events.events == []


# -- AC-17: which token is used -------------------------------------------------------------


def test_the_keyring_token_is_passed_to_the_runner_explicitly() -> None:
    controller, events, runner, tokens, _ = _controller(tokens=FakeTokenSource(SENTINEL))

    controller.download()
    events.wait_finished()

    assert runner.runs == [(SENTINEL, TIMEOUT)]
    assert tokens.reads == 1


def test_without_a_keyring_token_the_runner_gets_none_so_the_ambient_login_is_used() -> None:
    controller, events, runner, _, _ = _controller(tokens=FakeTokenSource(None))

    controller.download()
    events.wait_finished()

    assert runner.runs == [(None, TIMEOUT)]
    assert events.events[-1] == ("finished", ("READY", ""))


def test_a_locked_keyring_fails_the_download_and_never_runs_the_child() -> None:
    controller, events, runner, _, _ = _controller(tokens=FakeTokenSource(locked=True))

    reply = controller.download()
    events.wait_finished()

    assert reply == Success("DOWNLOADING")
    assert runner.runs == []
    assert events.events[-1] == ("finished", ("FAILED", "MODELS_TOKEN_KEYRING_LOCKED"))
    status = controller.status()
    assert (status.state, status.failure_code) == ("FAILED", "MODELS_TOKEN_KEYRING_LOCKED")


# -- failure codes ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (
            ModelsDownloadReportedError("DIARIZATION_MODEL_ACCESS_DENIED", "gated"),
            "DIARIZATION_MODEL_ACCESS_DENIED",
        ),
        (ModelsDownloadReportedError("MODEL_UNAVAILABLE", "offline"), "MODEL_UNAVAILABLE"),
        (ModelsDownloadReportedError("DIARIZATION_FAILED", "odd"), "MODELS_DOWNLOAD_FAILED"),
        (ModelsDownloadFailedError("exit 3"), "MODELS_DOWNLOAD_FAILED"),
        (ModelsDownloadTimeoutError("too slow"), "MODELS_DOWNLOAD_TIMEOUT"),
    ],
)
def test_a_failed_download_reports_its_code_in_the_signal_and_the_status(
    failure: Exception, code: str
) -> None:
    controller, events, _, _, _ = _controller(FakeRunner(failure=failure))  # type: ignore[arg-type]

    controller.download()
    events.wait_finished()

    assert events.events[-1] == ("finished", ("FAILED", code))
    status = controller.status()
    assert (status.state, status.failure_code) == ("FAILED", code)


def test_a_download_that_leaves_a_model_uncached_is_never_reported_as_ready() -> None:
    controller, events, _, _, _ = _controller(probe=FakeCacheProbe(NONE, CacheStatus(True, False)))

    controller.download()
    events.wait_finished()

    assert events.events[-1] == ("finished", ("FAILED", "MODELS_DOWNLOAD_FAILED"))
    status = controller.status()
    assert (status.state, status.whisper_cached, status.pipeline_cached) == (
        "FAILED",
        True,
        False,
    )


def test_a_new_download_clears_the_previous_failure() -> None:
    failing = FakeRunner(failure=ModelsDownloadFailedError("exit 3"))
    controller, events, _, _, _ = _controller(failing, probe=FakeCacheProbe(NONE, NONE, NONE, BOTH))
    controller.download()
    events.wait_finished()
    assert controller.status().state == "FAILED"
    failing.failure = None
    events.finished_event.clear()

    controller.download()
    events.wait_finished()

    assert controller.status().state == "READY"
    assert controller.status().failure_code == ""


def test_the_finished_event_comes_after_the_result_is_recorded() -> None:
    controller, _, _, _, _ = _controller()
    seen: list[str] = []

    class Peek(RecordingEvents):
        def finished(self, state: str, error_code: str) -> None:
            seen.append(f"{state}:{controller.status().state}:{controller.active}")
            super().finished(state, error_code)

    peek = Peek()
    controller.subscribe(peek)
    controller.download()
    peek.wait_finished()

    assert seen == ["READY:READY:False"]  # state recorded and the slot free before the signal


def test_shutdown_cancels_a_running_download_and_waits_for_it() -> None:
    gate = threading.Event()
    controller, _, runner, _, _ = _controller(FakeRunner(gate=gate))
    controller.download()
    assert runner.started.wait(5)

    assert controller.shutdown(5) is True
    assert not controller.active


def test_shutdown_with_nothing_running_is_true() -> None:
    controller, *_ = _controller()

    assert controller.shutdown(1) is True
