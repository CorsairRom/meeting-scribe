"""The "Speech models" state: status, token, download, progress, cancel (AC-19, spec install 6).

The token goes from the field to the keyring port and nowhere else: a sentinel proves it is in no
state. Late replies are matched to what asked for them, so none can override a newer outcome.
"""

import pytest

from meeting_scribe_review.core.errors import (
    KEYRING_STORE_FAILED,
    KEYRING_UNAVAILABLE,
    SERVICE_NOT_RUNNING,
    ServiceError,
)
from meeting_scribe_review.core.models import KeyStatus
from meeting_scribe_review.core.presenters.models import ModelsPhase, ModelsPresenter, ModelsState
from meeting_scribe_review.core.result import Err, Ok
from tests.unit.review_app.fakes import FakeModelsGateway, FakeTokenStore, models_status

SENTINEL = "hf_SENTINELtoken0123456789"


def _wire(
    *,
    deferred_gateway: bool = False,
    deferred_tokens: bool = False,
) -> tuple[ModelsPresenter, FakeModelsGateway, FakeTokenStore, list[ModelsState]]:
    gateway = FakeModelsGateway(deferred=deferred_gateway)
    tokens = FakeTokenStore(deferred=deferred_tokens)
    states: list[ModelsState] = []
    return ModelsPresenter(gateway, tokens, states.append), gateway, tokens, states


def _status(gateway: FakeModelsGateway, state: str = "PENDING", **kwargs: object) -> None:
    gateway.replies["models_status"] = Ok(models_status(state, **kwargs))  # type: ignore[arg-type]


# -- status ---------------------------------------------------------------------------------


def test_the_presenter_listens_to_the_service_once_and_dispose_stops_it() -> None:
    presenter, gateway, _, _ = _wire()
    assert gateway.listeners == [presenter]
    presenter.dispose()
    assert gateway.listeners == []


def test_nothing_is_asked_until_the_dialog_opens() -> None:
    presenter, gateway, tokens, _ = _wire()
    assert gateway.calls == []
    assert presenter.state.phase is ModelsPhase.LOADING
    assert not presenter.state.can_download
    assert tokens.stored == []


def test_ready_shows_ready_and_nothing_to_download() -> None:
    presenter, gateway, _, _ = _wire()
    _status(gateway, "READY", whisper=True, pipeline=True)
    presenter.open()
    state = presenter.state
    assert (state.phase, state.status_text) == (ModelsPhase.READY, "Ready")
    assert not state.can_download
    assert not state.show_progress


def test_pending_without_a_token_asks_for_the_token_and_still_allows_download() -> None:
    presenter, _, _, _ = _wire()
    presenter.open()
    state = presenter.state
    assert state.phase is ModelsPhase.PENDING
    assert state.token_status is KeyStatus.MISSING
    assert state.token_text == "No token stored"
    assert "token" in state.detail.lower()
    assert state.can_download  # an existing `hf auth login` is still used by the service


def test_pending_with_a_token_says_to_press_download() -> None:
    presenter, _, tokens, _ = _wire()
    tokens.stored.append("fake")
    presenter.open()
    state = presenter.state
    assert state.token_text == "Token stored"
    assert state.detail == "Press Download models."
    assert state.can_download


def test_downloading_from_the_status_shows_the_step_and_a_cancel() -> None:
    presenter, gateway, _, _ = _wire()
    _status(gateway, "DOWNLOADING", step=2, total=2)
    presenter.open()
    state = presenter.state
    assert state.phase is ModelsPhase.DOWNLOADING
    assert state.progress_text == "Downloading step 2 of 2"
    assert state.progress_fraction == 0.5  # completed steps over the total
    assert state.show_progress
    assert state.can_cancel
    assert not state.can_download


def test_downloading_without_progress_yet_says_starting_and_pulses() -> None:
    presenter, gateway, _, _ = _wire()
    _status(gateway, "DOWNLOADING")
    presenter.open()
    assert presenter.state.progress_text == "Starting the download..."
    assert presenter.state.progress_fraction is None


@pytest.mark.parametrize(
    "code",
    [
        "DIARIZATION_MODEL_ACCESS_DENIED",
        "MODEL_UNAVAILABLE",
        "MODELS_TOKEN_KEYRING_LOCKED",
        "MODELS_DOWNLOAD_TIMEOUT",
        "MODELS_DOWNLOAD_FAILED",
    ],
)
def test_a_failed_download_says_what_to_do_in_words(code: str) -> None:
    presenter, gateway, _, _ = _wire()
    _status(gateway, "FAILED", failure=code)
    presenter.open()
    state = presenter.state
    assert state.phase is ModelsPhase.FAILED
    assert state.status_text == "Download failed"
    assert code not in state.detail
    assert "Download models" in state.detail
    assert state.can_download  # a retry


def test_an_unknown_failure_code_still_names_the_code_and_allows_a_retry() -> None:
    presenter, gateway, _, _ = _wire()
    _status(gateway, "FAILED", failure="SOMETHING_NEW")
    presenter.open()
    assert "SOMETHING_NEW" in presenter.state.detail
    assert presenter.state.can_download


def test_an_unknown_state_is_an_answer_the_app_does_not_understand() -> None:
    presenter, gateway, _, _ = _wire()
    _status(gateway, "SOMETHING_NEW")
    presenter.open()
    assert presenter.state.phase is ModelsPhase.UNAVAILABLE
    assert not presenter.state.can_download


def test_a_service_that_is_not_running_says_so_with_the_command() -> None:
    presenter, gateway, _, _ = _wire()
    gateway.fail("models_status", SERVICE_NOT_RUNNING)
    presenter.open()
    state = presenter.state
    assert state.phase is ModelsPhase.UNAVAILABLE
    assert state.status_text == "The Meeting Scribe service is not running"
    assert state.hint == "systemctl --user start meeting-scribe.service"
    assert not state.can_download
    assert state.token_text == "No token stored"  # the keyring does not need the service


def test_an_older_service_asks_for_a_restart() -> None:
    presenter, gateway, _, _ = _wire()
    gateway.fail("models_status", "SERVICE_OUTDATED")
    presenter.open()
    assert "restarted" in presenter.state.status_text


def test_the_service_coming_back_reloads_and_going_away_shows_it() -> None:
    presenter, gateway, _, _ = _wire()
    presenter.open()
    presenter.on_service_available(False)
    assert presenter.state.phase is ModelsPhase.UNAVAILABLE
    presenter.on_service_available(True)
    assert gateway.count("models_status") == 2
    assert presenter.state.phase is ModelsPhase.PENDING


def test_availability_before_the_dialog_opens_asks_nothing() -> None:
    presenter, gateway, _, _ = _wire()
    presenter.on_service_available(True)
    presenter.on_service_available(False)
    assert gateway.calls == []


# -- download, progress, finish ---------------------------------------------------------------


def test_download_follows_the_progress_signals_to_ready() -> None:
    presenter, gateway, _, _ = _wire()
    presenter.open()
    presenter.download()
    assert gateway.count("download_models") == 1
    assert presenter.state.phase is ModelsPhase.DOWNLOADING
    assert presenter.state.can_cancel
    presenter.on_models_progress("pipeline", 1, 2)
    assert presenter.state.progress_text == "Downloading step 1 of 2"
    assert presenter.state.progress_fraction == 0.0
    presenter.on_models_progress("whisper", 2, 2)
    assert presenter.state.progress_text == "Downloading step 2 of 2"
    presenter.on_models_finished("READY", "")
    assert presenter.state.phase is ModelsPhase.READY
    assert not presenter.state.show_progress
    assert gateway.count("models_status") == 1  # the signal says it all: no second ask


def test_pressing_download_twice_asks_the_service_once() -> None:
    presenter, gateway, _, _ = _wire(deferred_gateway=True)
    presenter.open()
    gateway.release()  # the status
    presenter.download()
    presenter.download()
    assert gateway.count("download_models") == 1


def test_between_the_press_and_the_reply_it_says_starting_and_cannot_cancel_yet() -> None:
    presenter, gateway, _, _ = _wire()
    presenter.open()
    gateway.deferred = True
    presenter.download()
    state = presenter.state
    assert state.progress_text == "Starting the download..."
    assert not state.can_cancel  # the service refuses a cancel before its reply
    assert not state.can_download
    gateway.release()
    assert presenter.state.can_cancel


def test_download_when_already_cached_says_ready() -> None:
    presenter, gateway, _, _ = _wire()
    presenter.open()
    gateway.replies["download_models"] = Ok("READY")
    presenter.download()
    assert presenter.state.phase is ModelsPhase.READY


def test_a_progress_signal_that_beats_the_reply_wins_and_the_late_reply_is_dropped() -> None:
    presenter, gateway, _, _ = _wire()
    presenter.open()
    gateway.deferred = True
    presenter.download()
    presenter.on_models_progress("pipeline", 1, 2)
    assert presenter.state.can_cancel  # not stuck on "starting"
    gateway.release()  # the late "DOWNLOADING" reply
    assert presenter.state.progress_text == "Downloading step 1 of 2"


def test_a_failure_that_beats_the_reply_is_not_undone_by_it() -> None:
    presenter, gateway, _, _ = _wire()
    presenter.open()
    gateway.deferred = True
    presenter.download()
    presenter.on_models_finished("FAILED", "MODEL_UNAVAILABLE")
    gateway.release()
    assert presenter.state.phase is ModelsPhase.FAILED


def test_download_refused_because_one_is_running_follows_the_running_one() -> None:
    presenter, gateway, _, _ = _wire()
    presenter.open()
    gateway.fail("download_models", "MODELS_DOWNLOAD_ACTIVE")
    _status(gateway, "DOWNLOADING", step=1, total=2)
    presenter.download()
    assert presenter.state.phase is ModelsPhase.DOWNLOADING
    assert presenter.state.progress_text == "Downloading step 1 of 2"


def test_download_refused_for_another_reason_keeps_the_phase_and_says_why() -> None:
    presenter, gateway, _, _ = _wire()
    presenter.open()
    gateway.fail("download_models", "MODELS_DOWNLOAD_CANCELLED")  # the service is shutting down
    presenter.download()
    state = presenter.state
    assert state.phase is ModelsPhase.PENDING
    assert "shutting down" in state.detail
    assert state.can_download


def test_download_with_the_service_gone_shows_unavailable() -> None:
    presenter, gateway, _, _ = _wire()
    presenter.open()
    gateway.fail("download_models", SERVICE_NOT_RUNNING)
    presenter.download()
    assert presenter.state.phase is ModelsPhase.UNAVAILABLE


def test_a_failed_download_ends_failed_and_a_retry_clears_the_text() -> None:
    presenter, _, _, _ = _wire()
    presenter.open()
    presenter.download()
    presenter.on_models_finished("FAILED", "DIARIZATION_MODEL_ACCESS_DENIED")
    assert presenter.state.phase is ModelsPhase.FAILED
    assert "terms" in presenter.state.detail
    presenter.download()
    assert presenter.state.detail == ""
    assert presenter.state.phase is ModelsPhase.DOWNLOADING


def test_the_progress_of_a_partial_total_never_divides_by_zero() -> None:
    presenter, _, _, _ = _wire()
    presenter.open()
    presenter.on_models_progress("pipeline", 1, 0)
    assert presenter.state.progress_fraction is None


# -- cancel -----------------------------------------------------------------------------------


def test_cancel_says_cancelling_until_the_service_confirms() -> None:
    presenter, gateway, _, _ = _wire()
    _status(gateway, "DOWNLOADING", step=1, total=2)
    presenter.open()
    presenter.cancel()
    assert gateway.count("cancel_download") == 1
    state = presenter.state
    assert state.progress_text == "Cancelling..."
    assert not state.can_cancel
    presenter.on_models_finished("CANCELLED", "")
    state = presenter.state
    assert state.phase is ModelsPhase.CANCELLED
    assert state.status_text == "Download cancelled"
    assert "resume" in state.detail
    assert state.can_download
    assert not state.show_progress


def test_cancel_when_nothing_runs_any_more_asks_the_service_again() -> None:
    presenter, gateway, _, _ = _wire()
    _status(gateway, "DOWNLOADING", step=1, total=2)
    presenter.open()
    gateway.replies["cancel_download"] = Ok(False)
    _status(gateway, "READY", whisper=True, pipeline=True)
    presenter.cancel()
    assert presenter.state.phase is ModelsPhase.READY


def test_a_failed_cancel_says_why_and_can_be_pressed_again() -> None:
    presenter, gateway, _, _ = _wire()
    _status(gateway, "DOWNLOADING", step=1, total=2)
    presenter.open()
    gateway.fail("cancel_download", "UNKNOWN_ERROR")
    presenter.cancel()
    assert presenter.state.can_cancel
    assert presenter.state.detail


def test_a_cancel_is_ignored_when_no_download_is_shown() -> None:
    presenter, gateway, _, _ = _wire()
    presenter.open()
    presenter.cancel()
    assert gateway.count("cancel_download") == 0


def test_closing_the_dialog_does_not_leave_cancelling_behind() -> None:
    """The cancelling wait is local: it must not survive a closed dialog (an earlier pitfall)."""
    presenter, gateway, _, _ = _wire()
    _status(gateway, "DOWNLOADING", step=1, total=2)
    presenter.open()
    gateway.deferred = True
    presenter.cancel()
    assert presenter.state.progress_text == "Cancelling..."
    presenter.close()
    gateway.deferred = False
    presenter.open()
    assert presenter.state.progress_text == "Downloading step 1 of 2"
    assert presenter.state.can_cancel
    gateway.release()  # the reply of the old cancel: it belongs to a closed dialog
    assert presenter.state.can_cancel


def test_signals_while_the_dialog_is_closed_change_nothing_and_opening_reloads() -> None:
    presenter, gateway, _, states = _wire()
    presenter.open()
    presenter.close()
    seen = len(states)
    presenter.on_models_progress("pipeline", 1, 2)
    presenter.on_models_finished("FAILED", "MODELS_DOWNLOAD_FAILED")
    assert len(states) == seen
    _status(gateway, "READY", whisper=True, pipeline=True)
    presenter.open()
    assert presenter.state.phase is ModelsPhase.READY


def test_a_status_that_was_asked_before_a_failure_never_replaces_it() -> None:
    """A stale success after a newer failure (an earlier pitfall): the older answer is dropped."""
    presenter, gateway, _, _ = _wire(deferred_gateway=True)
    presenter.open()  # asks the status; the answer is held
    presenter.on_models_finished("FAILED", "MODELS_DOWNLOAD_FAILED")
    assert presenter.state.phase is ModelsPhase.FAILED
    gateway.release()  # the older status, "PENDING", arrives late
    assert presenter.state.phase is ModelsPhase.FAILED
    assert "Download models" in presenter.state.detail


def test_reopening_asks_again_and_drops_the_answer_of_the_closed_opening() -> None:
    presenter, gateway, _, _ = _wire(deferred_gateway=True)
    presenter.open()
    presenter.close()
    _status(gateway, "READY", whisper=True, pipeline=True)
    presenter.open()
    gateway.release(1)  # the second ask
    assert presenter.state.phase is ModelsPhase.READY
    gateway.release()  # the first ask (PENDING), held since before the close: it is dropped
    assert presenter.state.phase is ModelsPhase.READY


# -- the token --------------------------------------------------------------------------------


def test_an_empty_token_is_not_stored() -> None:
    presenter, _, tokens, _ = _wire()
    presenter.open()
    presenter.submit_token("   ")
    assert tokens.stored == []
    assert presenter.state.token_problem == "Enter the token."


def test_a_token_is_stored_in_the_keyring_trimmed_and_marked_stored() -> None:
    presenter, _, tokens, _ = _wire()
    presenter.open()
    assert presenter.state.token_text == "No token stored"
    presenter.submit_token(f"  {SENTINEL}\n")
    assert tokens.stored == [SENTINEL]
    state = presenter.state
    assert state.token_status is KeyStatus.STORED
    assert state.token_text == "Token stored"
    assert state.token_message == "Token saved."
    assert not state.token_saving
    assert state.token_problem == ""


def test_the_token_is_in_no_state_and_no_text_the_presenter_made() -> None:
    presenter, gateway, tokens, states = _wire()
    presenter.open()
    presenter.submit_token(SENTINEL)
    tokens.store_result = Err(ServiceError(KEYRING_STORE_FAILED))
    presenter.submit_token(SENTINEL)
    presenter.download()
    presenter.on_models_progress("pipeline", 1, 2)
    presenter.on_models_finished("FAILED", "DIARIZATION_MODEL_ACCESS_DENIED")
    assert states
    assert all(SENTINEL not in repr(state) for state in states)
    assert all(SENTINEL not in repr(call) for call in gateway.calls)  # no gateway call takes one
    kept = {k: v for k, v in vars(presenter).items() if k != "_tokens"}  # the fake keyring
    assert SENTINEL not in repr(kept)


def test_while_saving_the_field_is_busy_and_download_waits_for_it() -> None:
    presenter, _, tokens, _ = _wire(deferred_tokens=True)
    presenter.open()
    tokens.release()  # the keyring lookup of open()
    presenter.submit_token("fake")
    state = presenter.state
    assert state.token_saving
    assert not state.can_save_token
    assert not state.can_download  # the service reads the keyring: let the write finish first
    tokens.release()
    assert presenter.state.can_download
    assert presenter.state.can_save_token


def test_a_failed_store_says_why_and_keeps_the_old_indicator() -> None:
    presenter, _, tokens, _ = _wire()
    presenter.open()
    tokens.store_result = Err(ServiceError(KEYRING_STORE_FAILED))
    presenter.submit_token("fake")
    state = presenter.state
    assert state.token_status is KeyStatus.MISSING
    assert "could not be stored" in state.token_problem
    assert "token" in state.token_problem.lower()
    assert state.token_message == ""
    assert state.can_save_token


def test_an_unreachable_keyring_is_said_plainly() -> None:
    presenter, _, tokens, _ = _wire()
    presenter.open()
    tokens.store_result = Err(ServiceError(KEYRING_UNAVAILABLE))
    presenter.submit_token("fake")
    assert "keyring service is not reachable" in presenter.state.token_problem


def test_the_keyring_state_is_worded_for_a_locked_or_missing_keyring() -> None:
    presenter, _, tokens, _ = _wire()
    tokens.status = Ok(KeyStatus.LOCKED)
    presenter.open()
    assert presenter.state.token_text == "Keyring locked"
    tokens.status = Err(ServiceError(KEYRING_UNAVAILABLE))
    presenter.open()
    assert presenter.state.token_status is KeyStatus.UNAVAILABLE
    assert presenter.state.token_text == "Keyring unavailable"


def test_a_write_that_succeeds_after_the_dialog_closed_still_counts() -> None:
    """An earlier pitfall: a key stored after the editor was cancelled must not read as missing."""
    presenter, _, tokens, _ = _wire(deferred_tokens=True)
    presenter.open()
    tokens.release()
    presenter.submit_token("fake")
    presenter.close()
    tokens.release()  # the write finishes after the close
    assert presenter.state.token_status is KeyStatus.STORED
    presenter.open()
    assert presenter.state.token_saving is False


def test_a_late_success_after_a_newer_failure_keeps_the_failure_text() -> None:
    """An earlier pitfall: the stale success must not replace the newer attempt's failure text."""
    presenter, _, tokens, _ = _wire(deferred_tokens=True)
    presenter.open()
    tokens.release()
    presenter.submit_token("first")  # held
    presenter.close()
    presenter.open()
    tokens.release(1)  # the lookup of the second opening
    tokens.store_result = Err(ServiceError(KEYRING_STORE_FAILED))
    presenter.submit_token("second")
    tokens.release(1)  # the second attempt fails first
    failure = presenter.state.token_problem
    assert failure
    tokens.release()  # the first attempt succeeds late
    state = presenter.state
    assert state.token_problem == failure
    assert state.token_message == ""
    assert state.token_status is KeyStatus.STORED  # the fact is still kept


def test_a_late_failure_after_a_newer_success_changes_nothing() -> None:
    presenter, _, tokens, _ = _wire(deferred_tokens=True)
    presenter.open()
    tokens.release()
    tokens.store_result = Err(ServiceError(KEYRING_STORE_FAILED))
    presenter.submit_token("first")  # held, will fail
    presenter.close()
    presenter.open()
    tokens.release(1)
    tokens.store_result = Ok(None)
    presenter.submit_token("second")
    tokens.release(1)
    assert presenter.state.token_status is KeyStatus.STORED
    tokens.release()  # the late failure
    state = presenter.state
    assert state.token_status is KeyStatus.STORED
    assert state.token_problem == ""
    assert state.token_message == "Token saved."


def test_a_keyring_lookup_asked_before_a_store_never_undoes_it() -> None:
    presenter, _, tokens, _ = _wire(deferred_tokens=True)
    presenter.open()  # the lookup will answer "missing", held
    presenter.submit_token("fake")
    tokens.release(1)  # the store succeeds
    assert presenter.state.token_status is KeyStatus.STORED
    tokens.release()  # the older lookup answers "missing" late
    assert presenter.state.token_status is KeyStatus.STORED


def test_a_second_submit_while_saving_is_ignored() -> None:
    presenter, _, tokens, _ = _wire(deferred_tokens=True)
    presenter.open()
    tokens.release()
    presenter.submit_token("one")
    presenter.submit_token("two")
    assert tokens.stored == ["one"]
