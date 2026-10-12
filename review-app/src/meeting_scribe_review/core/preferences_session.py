"""One Preferences dialog's presenters, wired together without any widget.

The presenter owns the settings; the key editor reuses ``ApiKeyPresenter`` and, once a key is
saved, asks the presenter to look at the keyring again. The key never enters this class.
"""

from collections.abc import Callable
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
        self,
        gateway: SettingsGateway,
        keys: ApiKeyStore,
        views: PreferencesViews,
        after: Callable[[float, Callable[[], None]], None] | None = None,
    ) -> None:
        self._gateway = gateway
        self._views = views
        self._open = False
        self._preferences = PreferencesPresenter(gateway, keys, views.show_preferences, after)
        self._api_key = ApiKeyPresenter(keys, self._key_changed)
        gateway.watch_settings(self)

    @property
    def state(self) -> PreferencesState:
        return self._preferences.state

    def open(self) -> None:
        """The dialog opens: read the fields and the keyring once more (they may have changed)."""
        self._open = True
        self._preferences.load()

    def close(self) -> None:
        self._open = False
        self._api_key.cancel()

    def dispose(self) -> None:
        """The window is going away: stop listening to the service."""
        self._gateway.unwatch_settings(self)

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

    def refresh_models(self) -> None:
        self._preferences.refresh_models()

    def remove_key(self) -> None:
        self._preferences.remove_key()

    def on_service_available(self, available: bool) -> None:
        if self._open:  # nothing to keep in step while no dialog is showing
            self._preferences.on_service_available(available)

    def on_settings_changed(self, keys: tuple[str, ...]) -> None:
        if self._open:
            self._preferences.on_settings_changed(keys)

    def _key_changed(self, state: ApiKeyState) -> None:
        self._views.show_api_key(state)
        if state.status is ApiKeyStatus.SAVED:
            self._preferences.refresh_key()
