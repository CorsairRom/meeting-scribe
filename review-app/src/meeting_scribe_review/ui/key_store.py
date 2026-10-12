"""``ApiKeyStore`` on libsecret: the cloud key goes to the GNOME Keyring and nowhere else (BR-005).

The item is the one ``secret-tool`` makes and the service reads (same schema name, attributes and
label). The key never crosses the service's D-Bus API and is never logged: a failure logs a code
only. libsecret may prompt to unlock the keyring; that is acceptable because the user just asked
to store the key.
"""

import logging

from gi.repository import Gio, GLib

from meeting_scribe_review.core.constants import KEY_ATTRIBUTES, KEY_LABEL
from meeting_scribe_review.core.errors import (
    KEYRING_REMOVE_FAILED,
    KEYRING_STORE_FAILED,
    KEYRING_UNAVAILABLE,
    ServiceError,
)
from meeting_scribe_review.core.gateway import Callback
from meeting_scribe_review.core.models import KeyStatus
from meeting_scribe_review.core.result import Err, Ok

try:
    from gi.repository import Secret
except ImportError:  # no libsecret typelib: the field reports the keyring as unavailable
    Secret = None

_LOG = logging.getLogger(__name__)
_SCHEMA_NAME = "org.freedesktop.Secret.Generic"  # what secret-tool uses
_NO_SERVICE = (
    Gio.DBusError.SERVICE_UNKNOWN,
    Gio.DBusError.NAME_HAS_NO_OWNER,
    Gio.DBusError.NO_SERVER,
)


def _failure(error: GLib.Error, code_when_reachable: str) -> ServiceError:
    unreachable = any(error.matches(Gio.dbus_error_quark(), c) for c in _NO_SERVICE)
    return ServiceError(KEYRING_UNAVAILABLE if unreachable else code_when_reachable)


def _schema() -> "Secret.Schema":
    kinds = dict.fromkeys(KEY_ATTRIBUTES, Secret.SchemaAttributeType.STRING)
    return Secret.Schema.new(_SCHEMA_NAME, Secret.SchemaFlags.DONT_MATCH_NAME, kinds)


class SecretKeyStore:
    def store(self, secret: str, done: Callback[None]) -> None:
        if Secret is None:
            done(Err(ServiceError(KEYRING_UNAVAILABLE)))
            return

        def finished(_source: object, result: Gio.AsyncResult) -> None:
            try:
                Secret.password_store_finish(result)
            except GLib.Error as error:
                failure = _failure(error, KEYRING_STORE_FAILED)
                _LOG.warning("storing the api key failed: %s", failure.code)
                done(Err(failure))
                return
            done(Ok(None))

        Secret.password_store(
            _schema(), KEY_ATTRIBUTES, Secret.COLLECTION_DEFAULT, KEY_LABEL, secret, None, finished
        )

    def is_stored(self, done: Callback[KeyStatus]) -> None:
        """Search by attributes without loading secrets: the key is never read to show this."""
        if Secret is None:
            done(Err(ServiceError(KEYRING_UNAVAILABLE)))
            return

        def found(service: "Secret.Service", result: Gio.AsyncResult) -> None:
            try:
                items = service.search_finish(result)
            except GLib.Error as error:
                failure = _failure(error, KEYRING_UNAVAILABLE)
                _LOG.warning("looking for the api key failed: %s", failure.code)
                done(Err(failure))
                return
            if not items:
                done(Ok(KeyStatus.MISSING))
            else:
                done(Ok(KeyStatus.LOCKED if items[0].get_locked() else KeyStatus.STORED))

        def connected(_source: object, result: Gio.AsyncResult) -> None:
            try:
                service = Secret.Service.get_finish(result)
            except GLib.Error as error:
                failure = _failure(error, KEYRING_UNAVAILABLE)
                _LOG.warning("looking for the api key failed: %s", failure.code)
                done(Err(failure))
                return
            service.search(_schema(), KEY_ATTRIBUTES, Secret.SearchFlags.NONE, None, found)

        # ServiceFlags.NONE: no session is opened and no collection or item is loaded.
        Secret.Service.get(Secret.ServiceFlags.NONE, None, connected)

    def remove(self, done: Callback[None]) -> None:
        if Secret is None:
            done(Err(ServiceError(KEYRING_UNAVAILABLE)))
            return

        def finished(_source: object, result: Gio.AsyncResult) -> None:
            try:
                Secret.password_clear_finish(result)  # False: there was nothing to remove
            except GLib.Error as error:
                failure = _failure(error, KEYRING_REMOVE_FAILED)
                _LOG.warning("removing the api key failed: %s", failure.code)
                done(Err(failure))
                return
            done(Ok(None))

        Secret.password_clear(_schema(), KEY_ATTRIBUTES, None, finished)
