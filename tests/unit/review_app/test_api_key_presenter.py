"""The "Set API key" flow: the key goes to the keyring port and is never kept (BR-005)."""

from meeting_scribe_review.core.errors import KEYRING_UNAVAILABLE, ServiceError
from meeting_scribe_review.core.gateway import ApiKeyStore
from meeting_scribe_review.core.presenters.api_key import (
    SAVED_TEXT,
    ApiKeyPresenter,
    ApiKeyState,
    ApiKeyStatus,
)
from meeting_scribe_review.core.result import Err, Ok
from tests.unit.review_app.fakes import FakeApiKeyStore

FAKE_KEY = "fake-key-0123"


def _wire(
    store: FakeApiKeyStore | None = None,
) -> tuple[ApiKeyPresenter, FakeApiKeyStore, list[ApiKeyState]]:
    store = store or FakeApiKeyStore()
    states: list[ApiKeyState] = []
    return ApiKeyPresenter(store, states.append), store, states


def test_the_fake_store_is_an_api_key_store() -> None:
    assert isinstance(FakeApiKeyStore(), ApiKeyStore)


def test_opening_the_editor_stores_nothing() -> None:
    presenter, store, states = _wire()
    presenter.edit()
    assert states[-1].status is ApiKeyStatus.EDITING
    assert store.stored == []


def test_a_trimmed_key_goes_to_the_store_and_the_editor_closes() -> None:
    presenter, store, states = _wire()
    presenter.edit()
    presenter.submit(f"  {FAKE_KEY}\n")
    assert store.stored == [FAKE_KEY]
    assert states[-1].status is ApiKeyStatus.SAVED
    assert states[-1].message == SAVED_TEXT


def test_an_empty_key_is_refused_without_calling_the_store() -> None:
    presenter, store, _states = _wire()
    presenter.edit()
    presenter.submit("   ")
    assert store.stored == []
    assert presenter.state.status is ApiKeyStatus.EDITING
    assert presenter.state.problem == "Enter the API key."


def test_a_failed_store_keeps_the_editor_open_with_the_reason() -> None:
    store = FakeApiKeyStore(result=Err(ServiceError(KEYRING_UNAVAILABLE)))
    presenter, _, _states = _wire(store)
    presenter.edit()
    presenter.submit(FAKE_KEY)
    assert presenter.state.status is ApiKeyStatus.EDITING
    assert "keyring" in presenter.state.problem


def test_cancel_closes_without_storing() -> None:
    presenter, store, _states = _wire()
    presenter.edit()
    presenter.cancel()
    presenter.submit(FAKE_KEY)  # nothing is open any more
    assert store.stored == []
    assert presenter.state.status is ApiKeyStatus.IDLE


def test_the_key_is_never_kept_in_a_state_or_on_the_presenter() -> None:
    presenter, _store, states = _wire()
    presenter.edit()
    presenter.submit(FAKE_KEY)
    for state in states:
        assert FAKE_KEY not in repr(state)
    kept = {name: value for name, value in vars(presenter).items() if name != "_store"}
    assert FAKE_KEY not in repr(kept)


class _SlowStore:
    def __init__(self) -> None:
        self.pending: list[object] = []

    def store(self, secret: str, done: object) -> None:  # noqa: ARG002
        self.pending.append(done)


def test_a_late_successful_store_after_cancel_is_published_as_saved() -> None:
    store = _SlowStore()
    states: list[ApiKeyState] = []
    presenter = ApiKeyPresenter(store, states.append)  # type: ignore[arg-type]
    presenter.edit()
    presenter.submit(FAKE_KEY)
    presenter.cancel()
    store.pending[0](Ok(None))  # type: ignore[operator]
    assert presenter.state.status is ApiKeyStatus.SAVED


def test_a_late_store_result_of_an_earlier_attempt_is_ignored() -> None:
    store = _SlowStore()
    presenter = ApiKeyPresenter(store, lambda _state: None)  # type: ignore[arg-type]
    presenter.edit()
    presenter.submit(FAKE_KEY)
    presenter.cancel()
    presenter.edit()
    store.pending[0](Err(ServiceError(KEYRING_UNAVAILABLE)))  # type: ignore[operator]
    assert presenter.state == ApiKeyState(ApiKeyStatus.EDITING)


def test_a_successful_store_after_cancel_is_not_dropped_silently() -> None:
    """The key IS in the keyring, so the user hears about it even if the editor was closed."""
    store = _SlowStore()
    states: list[ApiKeyState] = []
    presenter = ApiKeyPresenter(store, states.append)  # type: ignore[arg-type]
    presenter.edit()
    presenter.submit(FAKE_KEY)
    presenter.cancel()
    store.pending[0](Ok(None))  # type: ignore[operator]
    assert presenter.state == ApiKeyState(ApiKeyStatus.SAVED, message=SAVED_TEXT)


def test_submit_cancel_edit_submit_with_out_of_order_completions() -> None:
    store = _SlowStore()
    presenter = ApiKeyPresenter(store, lambda _state: None)  # type: ignore[arg-type]
    presenter.edit()
    presenter.submit(FAKE_KEY)  # attempt 1
    presenter.cancel()
    presenter.edit()
    presenter.submit(FAKE_KEY)  # attempt 2
    assert presenter.state.status is ApiKeyStatus.SAVING
    store.pending[1](Ok(None))  # type: ignore[operator]  # the newer one finishes first
    assert presenter.state.status is ApiKeyStatus.SAVED
    store.pending[0](Err(ServiceError(KEYRING_UNAVAILABLE)))  # type: ignore[operator]
    assert presenter.state.status is ApiKeyStatus.SAVED  # a stale failure changes nothing


def test_a_stale_success_while_a_newer_attempt_is_saving_does_not_end_it() -> None:
    store = _SlowStore()
    presenter = ApiKeyPresenter(store, lambda _state: None)  # type: ignore[arg-type]
    presenter.edit()
    presenter.submit(FAKE_KEY)
    presenter.cancel()
    presenter.edit()
    presenter.submit(FAKE_KEY)
    store.pending[0](Ok(None))  # type: ignore[operator]
    assert presenter.state.status is ApiKeyStatus.SAVED  # the key is stored either way
    store.pending[1](Err(ServiceError(KEYRING_UNAVAILABLE)))  # type: ignore[operator]
    assert presenter.state.status is ApiKeyStatus.SAVED
