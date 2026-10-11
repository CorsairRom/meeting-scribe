"""The "Set API key" flow: the key goes to the keyring port and is never kept (BR-005)."""

from meeting_scribe_review.core.errors import KEYRING_UNAVAILABLE, ServiceError
from meeting_scribe_review.core.gateway import ApiKeyStore
from meeting_scribe_review.core.presenters.api_key import (
    KEY_STORED_TEXT,
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
    """Holds every completion so a test decides the order they arrive in."""

    def __init__(self) -> None:
        self.pending: list[object] = []

    def store(self, secret: str, done: object) -> None:  # noqa: ARG002
        self.pending.append(done)

    def finish(self, index: int, result: object) -> None:
        self.pending[index](result)  # type: ignore[operator]


def _slow() -> tuple[ApiKeyPresenter, _SlowStore, list[ApiKeyState]]:
    store = _SlowStore()
    states: list[ApiKeyState] = []
    return ApiKeyPresenter(store, states.append), store, states  # type: ignore[arg-type]


FAILURE = Err(ServiceError(KEYRING_UNAVAILABLE))


def _submit_cancel_edit_submit(presenter: ApiKeyPresenter) -> None:
    """Attempt A (index 0) is cancelled; attempt B (index 1) is the one in flight."""
    presenter.edit()
    presenter.submit(FAKE_KEY)
    presenter.cancel()
    presenter.edit()
    presenter.submit(FAKE_KEY)


def test_a_stale_failure_with_no_newer_attempt_changes_nothing() -> None:
    presenter, store, _ = _slow()
    presenter.edit()
    presenter.submit(FAKE_KEY)
    presenter.cancel()
    presenter.edit()
    store.finish(0, FAILURE)
    assert presenter.state == ApiKeyState(ApiKeyStatus.EDITING)


def test_a_stale_success_after_cancel_says_a_key_was_stored() -> None:
    """The key IS in the keyring, so the user hears it even though the editor was closed."""
    presenter, store, _ = _slow()
    presenter.edit()
    presenter.submit(FAKE_KEY)
    presenter.cancel()
    store.finish(0, Ok(None))
    assert presenter.state == ApiKeyState(ApiKeyStatus.IDLE, message=KEY_STORED_TEXT)


def test_a_stale_success_while_editing_says_stored_and_keeps_the_edit_open() -> None:
    presenter, store, _ = _slow()
    presenter.edit()
    presenter.submit(FAKE_KEY)
    presenter.cancel()
    presenter.edit()  # no newer submit
    store.finish(0, Ok(None))
    assert presenter.state == ApiKeyState(ApiKeyStatus.EDITING, message=KEY_STORED_TEXT)
    presenter.submit(FAKE_KEY)  # the open edit still works and decides its own result
    assert presenter.state.status is ApiKeyStatus.SAVING


def test_a_stale_success_never_ends_a_newer_attempt_that_then_fails() -> None:
    presenter, store, _ = _slow()
    _submit_cancel_edit_submit(presenter)
    store.finish(0, Ok(None))  # A, stale, while B is saving: ignored
    assert presenter.state.status is ApiKeyStatus.SAVING
    store.finish(1, FAILURE)  # B alone decides
    assert presenter.state.status is ApiKeyStatus.EDITING
    assert "keyring" in presenter.state.problem


def test_the_newer_attempt_saves_once_and_a_late_stale_success_adds_nothing() -> None:
    presenter, store, states = _slow()
    _submit_cancel_edit_submit(presenter)
    store.finish(1, Ok(None))
    store.finish(0, Ok(None))
    assert presenter.state == ApiKeyState(ApiKeyStatus.SAVED, message=SAVED_TEXT)
    assert [s.status for s in states].count(ApiKeyStatus.SAVED) == 1


def test_a_stale_failure_never_changes_a_newer_attempt() -> None:
    presenter, store, _ = _slow()
    _submit_cancel_edit_submit(presenter)
    store.finish(0, FAILURE)
    assert presenter.state.status is ApiKeyStatus.SAVING
    store.finish(1, Ok(None))
    assert presenter.state.status is ApiKeyStatus.SAVED
