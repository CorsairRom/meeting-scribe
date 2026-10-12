"""Every cloud request asks first; only the explicit action sends it (AC-17, safety rule 3)."""

from meeting_scribe_review.core.presenters.cloud_confirmation import (
    CANCEL_LABEL,
    CONFIRM_LABEL,
    CloudConfirmationPresenter,
    ConfirmationRequest,
    ConfirmationState,
)

REQUEST = ConfirmationRequest("m1", "cloud", "cloud: Fake Gateway, fake-model")


def _presenter() -> tuple[CloudConfirmationPresenter, list[ConfirmationState], list[object]]:
    states: list[ConfirmationState] = []
    sent: list[object] = []
    return CloudConfirmationPresenter(states.append, sent.append), states, sent


def test_asking_opens_a_dialog_that_names_the_provider_and_the_names() -> None:
    presenter, states, sent = _presenter()
    presenter.ask(REQUEST)
    state = states[-1]
    assert state.open
    assert "leave this computer" in state.body
    assert "cloud: Fake Gateway, fake-model" in state.body
    assert "speaker names" in state.body
    assert state.confirm_label == CONFIRM_LABEL == "Send to cloud"
    assert state.cancel_label == CANCEL_LABEL == "Cancel"
    assert sent == []


def test_only_confirm_sends_and_it_sends_once() -> None:
    presenter, states, sent = _presenter()
    presenter.ask(REQUEST)
    presenter.confirm()
    presenter.confirm()
    assert sent == [REQUEST]
    assert not states[-1].open


def test_cancel_and_close_send_nothing() -> None:
    presenter, states, sent = _presenter()
    presenter.ask(REQUEST)
    presenter.cancel()
    presenter.confirm()
    assert sent == []
    assert not states[-1].open


def test_every_request_asks_again() -> None:
    presenter, states, sent = _presenter()
    presenter.ask(REQUEST)
    presenter.confirm()
    presenter.ask(REQUEST)
    assert states[-1].open
    assert sent == [REQUEST]


def test_a_second_ask_while_open_replaces_the_first() -> None:
    presenter, _states, sent = _presenter()
    other = ConfirmationRequest("m2", "cloud", "cloud: Other")
    presenter.ask(REQUEST)
    presenter.ask(other)
    presenter.confirm()
    assert sent == [other]
