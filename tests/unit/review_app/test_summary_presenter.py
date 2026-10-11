"""The Summary view of one meeting: states, the single button, progress, cancel, stale (AC-17)."""

from meeting_scribe_review.core.presenters.cloud_confirmation import ConfirmationRequest
from meeting_scribe_review.core.presenters.summary import (
    STALE_TEXT,
    SummaryPhase,
    SummaryPresenter,
    SummaryState,
)
from meeting_scribe_review.core.result import Ok
from tests.unit.review_app.fakes import (
    FakeSummariesGateway,
    provider,
    summary_document,
    summary_info,
)


def _wire(
    gateway: FakeSummariesGateway | None = None,
) -> tuple[SummaryPresenter, FakeSummariesGateway, list[SummaryState], list[ConfirmationRequest]]:
    gateway = gateway or FakeSummariesGateway()
    states: list[SummaryState] = []
    asked: list[ConfirmationRequest] = []
    presenter = SummaryPresenter(gateway, states.append, asked.append)
    return presenter, gateway, states, asked


def test_a_meeting_without_a_summary_offers_the_button_named_after_the_local_model() -> None:
    presenter, _gateway, _states, _asked = _wire()
    presenter.open("m1", "DONE")
    state = presenter.state
    assert state.phase is SummaryPhase.NONE
    assert state.button_label == "Generate summary: Local (fake-model)"
    assert state.can_generate
    assert not state.can_cancel


def test_a_meeting_that_is_not_done_shows_nothing() -> None:
    presenter, gateway, _states, _asked = _wire()
    presenter.open("m1", "TRANSCRIBING")
    assert presenter.state.phase is SummaryPhase.IDLE
    assert gateway.calls == []


def test_a_local_press_starts_the_summary_without_a_dialog() -> None:
    presenter, gateway, _states, asked = _wire()
    presenter.open("m1", "DONE")
    presenter.request()
    assert gateway.args_of("summarize") == [("m1", "local", False)]
    assert asked == []
    assert presenter.state.phase is SummaryPhase.RUNNING
    assert presenter.state.can_cancel
    assert not presenter.state.can_generate


def test_a_cloud_press_asks_and_sends_nothing_until_confirmed() -> None:
    gateway = FakeSummariesGateway()
    gateway.replies["providers"] = Ok(
        [provider("local", default=False), provider("cloud", cloud=True)]
    )
    presenter, _, _states, asked = _wire(gateway)
    presenter.open("m1", "DONE")
    assert "cloud" in presenter.state.button_label
    presenter.request()
    assert asked == [ConfirmationRequest("m1", "cloud", "cloud: Fake Gateway, fake-model")]
    assert gateway.args_of("summarize") == []
    presenter.send(asked[0])
    assert gateway.args_of("summarize") == [("m1", "cloud", True)]


def test_a_confirmation_for_another_meeting_is_ignored() -> None:
    presenter, gateway, _states, _asked = _wire()
    presenter.open("m1", "DONE")
    presenter.send(ConfirmationRequest("m2", "cloud", "cloud: x"))
    assert gateway.args_of("summarize") == []


def test_an_unavailable_default_provider_disables_the_button_with_its_reason() -> None:
    gateway = FakeSummariesGateway()
    gateway.replies["providers"] = Ok(
        [provider("cloud", cloud=True, available=False, reason="SUMMARY_CLOUD_DISABLED")]
    )
    presenter, _, _states, asked = _wire(gateway)
    presenter.open("m1", "DONE")
    state = presenter.state
    assert not state.can_generate
    assert state.message == "The cloud provider is turned off in the settings."
    presenter.request()
    assert asked == []
    assert gateway.args_of("summarize") == []


def test_no_default_provider_disables_the_button() -> None:
    gateway = FakeSummariesGateway()
    gateway.replies["providers"] = Ok([])
    presenter, _, _states, _asked = _wire(gateway)
    presenter.open("m1", "DONE")
    assert not presenter.state.can_generate
    assert presenter.state.button_label == "Generate summary"


def test_a_saved_local_summary_is_shown_with_copy_and_save() -> None:
    gateway = FakeSummariesGateway()
    gateway.replies["get_summary_status"] = Ok(summary_info("DONE", generated=True))
    presenter, _, _states, _asked = _wire(gateway)
    presenter.open("m1", "DONE")
    state = presenter.state
    assert state.phase is SummaryPhase.READY
    assert state.title == "Fake title"
    assert state.text == "# Fake title\n\nFake body."
    assert state.source_text == "Local (fake-model)"
    assert not state.is_cloud
    assert state.can_copy
    assert state.filename == "summary-m1.md"
    assert state.can_generate  # regenerate


def test_a_cloud_summary_keeps_its_badge_with_the_provider_name() -> None:
    gateway = FakeSummariesGateway()
    gateway.replies["get_summary_status"] = Ok(summary_info("DONE", generated=True, cloud=True))
    gateway.replies["get_summary"] = Ok(summary_document(cloud=True))
    presenter, _, _states, _asked = _wire(gateway)
    presenter.open("m1", "DONE")
    assert presenter.state.is_cloud
    assert "cloud: Fake Gateway" in presenter.state.source_text


def test_a_stale_summary_shows_the_banner_and_keeps_the_text() -> None:
    gateway = FakeSummariesGateway()
    gateway.replies["get_summary_status"] = Ok(summary_info("DONE", generated=True, stale=True))
    presenter, _, _states, _asked = _wire(gateway)
    presenter.open("m1", "DONE")
    assert presenter.state.stale_text == STALE_TEXT
    assert presenter.state.text


def test_speakers_changed_refreshes_the_stale_flag_without_reloading_the_text() -> None:
    gateway = FakeSummariesGateway()
    gateway.replies["get_summary_status"] = Ok(summary_info("DONE", generated=True))
    presenter, _, _states, _asked = _wire(gateway)
    presenter.open("m1", "DONE")
    gateway.replies["get_summary_status"] = Ok(summary_info("DONE", generated=True, stale=True))
    presenter.on_speakers_changed("m1")
    assert presenter.state.stale_text == STALE_TEXT
    assert len(gateway.args_of("get_summary")) == 1
    presenter.on_speakers_changed("other")
    assert len(gateway.args_of("get_summary_status")) == 2


def test_progress_signals_drive_the_running_state() -> None:
    presenter, _gateway, _states, _asked = _wire()
    presenter.open("m1", "DONE")
    presenter.request()
    assert presenter.state.progress_text == "Starting…"
    assert presenter.state.progress_fraction is None
    presenter.on_summary_progress("m1", 1, 4)
    assert presenter.state.progress_text == "Step 1 of 4"
    assert presenter.state.progress_fraction == 0.25
    presenter.on_summary_progress("other", 3, 4)
    assert presenter.state.progress_text == "Step 1 of 4"


def test_a_running_summary_found_on_open_shows_its_progress() -> None:
    gateway = FakeSummariesGateway()
    gateway.replies["get_summary_status"] = Ok(
        summary_info("RUNNING", step=2, total_steps=5, generated=False)
    )
    presenter, _, _states, _asked = _wire(gateway)
    presenter.open("m1", "DONE")
    assert presenter.state.phase is SummaryPhase.RUNNING
    assert presenter.state.progress_text == "Step 2 of 5"


def test_finishing_loads_the_new_summary() -> None:
    presenter, gateway, _states, _asked = _wire()
    presenter.open("m1", "DONE")
    presenter.request()
    gateway.replies["get_summary_status"] = Ok(summary_info("DONE", generated=True))
    presenter.on_summary_finished("m1", "DONE", "")
    assert presenter.state.phase is SummaryPhase.READY
    assert presenter.state.text


def test_a_failed_run_shows_the_message_and_keeps_the_previous_summary() -> None:
    gateway = FakeSummariesGateway()
    gateway.replies["get_summary_status"] = Ok(summary_info("DONE", generated=True))
    presenter, _, _states, _asked = _wire(gateway)
    presenter.open("m1", "DONE")
    presenter.request()
    gateway.replies["get_summary_status"] = Ok(
        summary_info("FAILED", generated=True, failure_code="SUMMARY_TIMEOUT")
    )
    presenter.on_summary_finished("m1", "FAILED", "SUMMARY_TIMEOUT")
    state = presenter.state
    assert state.phase is SummaryPhase.FAILED
    assert state.message == "The model took too long to answer. Try again."
    assert state.text
    assert state.can_generate


def test_a_failed_first_run_has_no_text_and_can_retry() -> None:
    presenter, gateway, _states, _asked = _wire()
    presenter.open("m1", "DONE")
    presenter.request()
    gateway.replies["get_summary_status"] = Ok(
        summary_info("FAILED", failure_code="SUMMARY_PROVIDER_UNAVAILABLE")
    )
    presenter.on_summary_finished("m1", "FAILED", "SUMMARY_PROVIDER_UNAVAILABLE")
    assert presenter.state.phase is SummaryPhase.FAILED
    assert not presenter.state.can_copy
    assert presenter.state.can_generate


def test_a_refused_start_shows_the_message_for_its_code() -> None:
    presenter, gateway, _states, _asked = _wire()
    presenter.open("m1", "DONE")
    gateway.fail("summarize", "SUMMARY_GPU_BUSY")
    presenter.request()
    assert presenter.state.phase is SummaryPhase.FAILED
    assert "GPU" in presenter.state.message
    assert presenter.state.can_generate


def test_a_start_refused_because_it_already_runs_shows_the_running_state() -> None:
    presenter, gateway, _states, _asked = _wire()
    presenter.open("m1", "DONE")
    gateway.fail("summarize", "SUMMARY_ALREADY_RUNNING")
    gateway.replies["get_summary_status"] = Ok(summary_info("RUNNING", step=1, total_steps=2))
    presenter.request()
    assert presenter.state.phase is SummaryPhase.RUNNING


def test_a_missing_key_asks_to_set_it() -> None:
    presenter, gateway, _states, _asked = _wire()
    presenter.open("m1", "DONE")
    gateway.fail("summarize", "SUMMARY_CLOUD_KEY_MISSING")
    presenter.request()
    assert presenter.state.needs_api_key
    gateway.fail("summarize", "SUMMARY_GPU_BUSY")
    presenter.request()
    assert not presenter.state.needs_api_key


def test_cancel_calls_the_service_once_and_waits_for_the_signal() -> None:
    presenter, gateway, _states, _asked = _wire()
    presenter.open("m1", "DONE")
    presenter.request()
    presenter.cancel()
    presenter.cancel()
    assert gateway.args_of("cancel_summary") == [("m1",)]
    assert presenter.state.progress_text == "Cancelling…"
    assert not presenter.state.can_cancel
    gateway.replies["get_summary_status"] = Ok(
        summary_info("FAILED", failure_code="SUMMARY_CANCELLED")
    )
    presenter.on_summary_finished("m1", "CANCELLED", "SUMMARY_CANCELLED")
    assert presenter.state.phase is SummaryPhase.FAILED
    assert presenter.state.message == "The summary was cancelled."


def test_cancel_does_nothing_when_idle() -> None:
    presenter, gateway, _states, _asked = _wire()
    presenter.open("m1", "DONE")
    presenter.cancel()
    assert gateway.args_of("cancel_summary") == []


def test_a_late_reply_for_a_meeting_already_left_is_dropped() -> None:
    gateway = FakeSummariesGateway(deferred=True)
    presenter, _, _states, _asked = _wire(gateway)
    presenter.open("m1", "DONE")
    presenter.open("m2", "DONE")
    gateway.release(0)
    gateway.release(0)
    assert presenter.state.meeting_id == "m2"
    assert presenter.state.phase is SummaryPhase.LOADING


def test_a_load_failure_is_an_error_state() -> None:
    gateway = FakeSummariesGateway()
    gateway.fail("get_summary_status", "SERVICE_OUTDATED")
    presenter, _, _states, _asked = _wire(gateway)
    presenter.open("m1", "DONE")
    assert presenter.state.phase is SummaryPhase.ERROR
    assert "restarted" in presenter.state.message


def test_processing_again_hides_the_view_and_done_brings_it_back() -> None:
    gateway = FakeSummariesGateway()
    gateway.replies["get_summary_status"] = Ok(summary_info("DONE", generated=True))
    presenter, _, _states, _asked = _wire(gateway)
    presenter.open("m1", "DONE")
    presenter.on_processing_state_changed("m1", "TRANSCRIBING")
    assert presenter.state.phase is SummaryPhase.IDLE
    presenter.on_processing_state_changed("m1", "DONE")
    assert presenter.state.phase is SummaryPhase.READY


def test_close_forgets_the_text() -> None:
    gateway = FakeSummariesGateway()
    gateway.replies["get_summary_status"] = Ok(summary_info("DONE", generated=True))
    presenter, _, _states, _asked = _wire(gateway)
    presenter.open("m1", "DONE")
    presenter.close()
    assert presenter.state == SummaryState()


def test_service_unavailable_then_back_reloads() -> None:
    presenter, gateway, _states, _asked = _wire()
    presenter.open("m1", "DONE")
    presenter.on_service_available(False)
    assert presenter.state.phase is SummaryPhase.ERROR
    presenter.on_service_available(True)
    assert presenter.state.phase is SummaryPhase.NONE
    assert len(gateway.args_of("providers")) == 2


def test_the_set_key_action_is_offered_only_for_an_available_cloud_provider() -> None:
    presenter, _gateway, _states, _asked = _wire()
    presenter.open("m1", "DONE")
    assert not presenter.state.can_set_key
    gateway = FakeSummariesGateway()
    gateway.replies["providers"] = Ok([provider("cloud", cloud=True)])
    presenter, _, _states, _asked = _wire(gateway)
    presenter.open("m1", "DONE")
    assert presenter.state.can_set_key
    gateway.replies["providers"] = Ok(
        [provider("cloud", cloud=True, available=False, reason="SUMMARY_CLOUD_DISABLED")]
    )
    presenter.open("m1", "DONE")
    assert not presenter.state.can_set_key


def test_a_service_restart_mid_run_does_not_leave_the_tab_running() -> None:
    presenter, _gateway, _states, _asked = _wire()
    presenter.open("m1", "DONE")
    presenter.request()
    presenter.on_summary_progress("m1", 1, 3)
    presenter.on_service_available(False)
    presenter.on_service_available(True)
    state = presenter.state
    assert state.phase is SummaryPhase.NONE
    assert state.can_generate
    assert state.progress_text == ""


def test_a_service_restart_while_cancelling_does_not_leave_cancelling() -> None:
    presenter, _gateway, _states, _asked = _wire()
    presenter.open("m1", "DONE")
    presenter.request()
    presenter.cancel()
    presenter.on_service_available(False)
    presenter.on_service_available(True)
    assert presenter.state.phase is SummaryPhase.NONE
    assert presenter.state.progress_text == ""


def test_a_run_still_going_after_a_reload_is_running_because_the_service_says_so() -> None:
    gateway = FakeSummariesGateway()
    gateway.replies["get_summary_status"] = Ok(summary_info("RUNNING", step=1, total_steps=2))
    presenter, _, _states, _asked = _wire(gateway)
    presenter.open("m1", "DONE")
    presenter.on_service_available(True)
    assert presenter.state.phase is SummaryPhase.RUNNING
    assert presenter.state.progress_text == "Step 1 of 2"


def _cloud_gateway(*ids: str) -> FakeSummariesGateway:
    gateway = FakeSummariesGateway()
    gateway.replies["providers"] = Ok(
        [provider(i, cloud=True, default=n == 0) for n, i in enumerate(ids)]
    )
    return gateway


def test_a_confirmation_for_a_provider_that_is_no_longer_the_default_asks_again() -> None:
    gateway = _cloud_gateway("cloud")
    presenter, _, _states, asked = _wire(gateway)
    presenter.open("m1", "DONE")
    presenter.request()
    old = asked[0]
    gateway.replies["providers"] = Ok([provider("cloud2", cloud=True)])
    presenter.on_service_available(True)
    presenter.send(old)
    assert gateway.args_of("summarize") == []
    assert [request.provider_id for request in asked] == ["cloud", "cloud2"]


def test_a_confirmation_is_dropped_when_the_default_is_no_longer_the_cloud() -> None:
    gateway = _cloud_gateway("cloud")
    presenter, _, _states, asked = _wire(gateway)
    presenter.open("m1", "DONE")
    presenter.request()
    gateway.replies["providers"] = Ok([provider("local")])
    presenter.on_service_available(True)
    presenter.send(asked[0])
    assert gateway.args_of("summarize") == []
    assert len(asked) == 1


def test_a_re_confirmed_cloud_request_completes_with_the_new_provider() -> None:
    gateway = _cloud_gateway("cloud")
    presenter, _, _states, asked = _wire(gateway)
    presenter.open("m1", "DONE")
    presenter.request()
    gateway.replies["providers"] = Ok([provider("cloud2", cloud=True)])
    presenter.on_service_available(True)
    presenter.send(asked[0])  # stale: asks again
    presenter.send(asked[1])  # the re-confirmation
    assert gateway.args_of("summarize") == [("m1", "cloud2", True)]
