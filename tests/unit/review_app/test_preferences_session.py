"""The Preferences session wires the presenter, the key editor and the gateway events."""

from dataclasses import replace

from meeting_scribe_review.core.models import KeyStatus
from meeting_scribe_review.core.preferences_session import PreferencesSession
from meeting_scribe_review.core.presenters.api_key import ApiKeyState, ApiKeyStatus
from meeting_scribe_review.core.presenters.preferences import PreferencesState
from tests.unit.review_app.fakes import FakeApiKeyStore, FakeSettingsGateway


class _Views:
    def __init__(self) -> None:
        self.preferences: list[PreferencesState] = []
        self.api_key: list[ApiKeyState] = []

    def show_preferences(self, state: PreferencesState) -> None:
        self.preferences.append(state)

    def show_api_key(self, state: ApiKeyState) -> None:
        self.api_key.append(state)


def _wire() -> tuple[PreferencesSession, FakeSettingsGateway, FakeApiKeyStore, _Views]:
    gateway, keys, views = FakeSettingsGateway(), FakeApiKeyStore(), _Views()
    return PreferencesSession(gateway, keys, views), gateway, keys, views


def test_the_session_listens_to_the_service_once() -> None:
    session, gateway, _, _ = _wire()
    assert gateway.listeners == [session]


def test_open_loads_and_a_signal_reloads() -> None:
    session, gateway, _, views = _wire()
    session.open()
    assert len(gateway.args_of("get_all")) == 1
    session.on_settings_changed(("summaries.language",))
    assert len(gateway.args_of("get_all")) == 2
    assert views.preferences[-1].fields


def test_storing_a_key_goes_to_the_keyring_and_refreshes_the_indicator() -> None:
    session, _, keys, views = _wire()
    session.open()
    assert views.preferences[-1].key_status is KeyStatus.MISSING

    session.set_api_key()
    session.submit_api_key("fake-key-0123")

    assert keys.stored == ["fake-key-0123"]
    assert views.api_key[-1].status is ApiKeyStatus.SAVED
    assert views.preferences[-1].key_status is KeyStatus.STORED
    assert all("fake-key-0123" not in repr(state) for state in views.preferences)


def test_cancelling_the_key_editor_stores_nothing() -> None:
    session, _, keys, views = _wire()
    session.open()
    session.set_api_key()
    session.cancel_api_key()
    assert keys.stored == []
    assert views.api_key[-1].status is ApiKeyStatus.IDLE


def test_remove_and_restart_and_save_are_forwarded() -> None:
    session, gateway, keys, views = _wire()
    keys.stored.append("fake")
    gateway.pending = ("storage.meetings_dir",)
    session.open()
    session.remove_key()
    assert keys.removed == 1
    session.edit("summaries.language", "es")
    session.save()
    assert gateway.args_of("set_values") == [({"summaries.language": "es"},)]
    session.restart()
    assert gateway.args_of("restart") == [()]
    assert views.preferences[-1].restarting


def test_close_cancels_an_open_key_editor() -> None:
    session, _, _, views = _wire()
    session.open()
    session.set_api_key()
    session.close()
    assert views.api_key[-1].status is ApiKeyStatus.IDLE


def test_availability_before_the_dialog_opens_loads_nothing() -> None:
    session, gateway, _, _ = _wire()
    session.on_service_available(True)
    assert gateway.args_of("get_all") == []


def test_dispose_stops_listening_to_the_service() -> None:
    session, gateway, _, _ = _wire()
    session.dispose()
    assert gateway.listeners == []


def test_a_stored_key_refreshes_the_cloud_models_and_refresh_asks_again() -> None:
    session, gateway, keys, _ = _wire()
    gateway.fields[0] = replace(gateway.fields[0], value="cloud")
    session.open()
    assert ("cloud",) not in gateway.args_of("list_models")  # no key yet

    session.set_api_key()
    session.submit_api_key("fake-key")

    assert keys.stored == ["fake-key"]
    assert gateway.args_of("list_models").count(("cloud",)) == 1
    session.refresh_models()
    assert gateway.args_of("list_models").count(("cloud",)) == 2
