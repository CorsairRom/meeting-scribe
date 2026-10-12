"""One Preferences dialog's presenters, wired together without any widget.

The presenter owns the settings; the key editor reuses ``ApiKeyPresenter`` and, once a key is
saved, asks the presenter to look at the keyring again. The key never enters this class.
"""

from typing import Protocol

from meeting_scribe_review.core.gateway import ApiKeyStore, SettingsGateway
from meeting_scribe_review.core.presenters.api_key import (
    ApiKeyPresenter,
    ApiKeyState,
    ApiKeyStatus,
)
from meeting_scribe_review.core.presenters.preferences import (
    PreferencesPresenter,
    PreferencesState,
)


class PreferencesViews(Protocol):
    def show_preferences(self, state: PreferencesState) -> None: ...

    def show_api_key(self, state: ApiKeyState) -> None: ...


class PreferencesSession:
    """Also the settings listener: the gateway keeps it for as long as the window lives."""

    def __init__(
        self, gateway: SettingsGateway, keys: ApiKeyStore, views: PreferencesViews
    ) -> None:
        self._views = views
        self._preferences = PreferencesPresenter(gateway, keys, views.show_preferences)
        self._api_key = ApiKeyPresenter(keys, self._key_changed)
        gateway.watch_settings(self)

    @property
    def state(self) -> PreferencesState:
        return self._preferences.state

    def open(self) -> None:
        """The dialog opens: read the fields and the keyring once more (they may have changed)."""
        self._preferences.load()

    def close(self) -> None:
        self._api_key.cancel()

    def edit(self, key: str, value: str) -> None:
        self._preferences.edit(key, value)

    def save(self) -> None:
        self._preferences.save()

    def restart(self) -> None:
        self._preferences.restart()

    def set_api_key(self) -> None:
        self._api_key.edit()

    def submit_api_key(self, raw: str) -> None:
        self._api_key.submit(raw)

    def cancel_api_key(self) -> None:
        self._api_key.cancel()

    def remove_key(self) -> None:
        self._preferences.remove_key()

    def on_service_available(self, available: bool) -> None:
        self._preferences.on_service_available(available)

    def on_settings_changed(self, keys: tuple[str, ...]) -> None:
        self._preferences.on_settings_changed(keys)

    def _key_changed(self, state: ApiKeyState) -> None:
        self._views.show_api_key(state)
        if state.status is ApiKeyStatus.SAVED:
            self._preferences.refresh_key()
