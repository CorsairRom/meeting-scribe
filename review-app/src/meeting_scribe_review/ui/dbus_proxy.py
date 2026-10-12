"""One ``Gio.DBusProxy`` for one interface of the service, with asynchronous calls.

Every call answers on the GLib main loop, exactly once, and may wake the service through bus
activation. Nothing here logs a name, a transcript or a summary: a failed call logs the method and
the error code only.
"""

import logging
from collections.abc import Callable
from typing import Any

from gi.repository import Gio, GLib

from meeting_scribe_review.core.constants import BUS_NAME, OBJECT_PATH
from meeting_scribe_review.core.errors import INVALID_REPLY, SERVICE_NOT_RUNNING, ServiceError
from meeting_scribe_review.core.gateway import Callback
from meeting_scribe_review.core.result import Err, Result
from meeting_scribe_review.ui.dbus_codec import error_from_glib, pack_args, unpack_reply

_LOG = logging.getLogger(__name__)
_CALL_TIMEOUT_MS = 30_000

type _Parser[T] = Callable[[Any], Result[T, ServiceError]]


class InterfaceProxy:
    def __init__(
        self,
        bus_type: Gio.BusType,
        interface: str,
        on_signal: Callable[[str, Any], None],
    ) -> None:
        self._bus_type = bus_type
        self._interface = interface
        self._on_signal = on_signal
        self._proxy: Gio.DBusProxy | None = None
        self._creating = False
        self._waiting: list[Callable[[Gio.DBusProxy | None], None]] = []

    def with_proxy(self, then: Callable[[Gio.DBusProxy | None], None]) -> None:
        if self._proxy is not None:
            then(self._proxy)
            return
        self._waiting.append(then)
        if self._creating:
            return
        self._creating = True
        # Not started at construction: a missing service must not be woken just by watching it.
        # A call still starts it (bus activation).
        Gio.DBusProxy.new_for_bus(
            self._bus_type,
            Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES
            | Gio.DBusProxyFlags.DO_NOT_AUTO_START_AT_CONSTRUCTION,
            None,
            BUS_NAME,
            OBJECT_PATH,
            self._interface,
            None,
            self._proxy_ready,
        )

    def _proxy_ready(self, _source: Any, result: Gio.AsyncResult) -> None:
        self._creating = False
        try:
            proxy = Gio.DBusProxy.new_for_bus_finish(result)
        except GLib.Error as error:
            _LOG.warning("no proxy: %s", error_from_glib(error).code)
            proxy = None
        else:
            proxy.connect("g-signal", self._signal)
            self._proxy = proxy
        waiting, self._waiting = self._waiting, []
        for then in waiting:
            then(proxy)

    def _signal(
        self, _proxy: Gio.DBusProxy, _sender: str | None, name: str, parameters: GLib.Variant
    ) -> None:
        self._on_signal(name, parameters.unpack())

    def call[T](
        self,
        method: str,
        signature: str,
        values: tuple[Any, ...],
        parse: _Parser[T],
        done: Callback[T],
        timeout_ms: int = _CALL_TIMEOUT_MS,
    ) -> None:
        def finished(proxy: Gio.DBusProxy, result: Gio.AsyncResult) -> None:
            try:
                reply = proxy.call_finish(result)
            except GLib.Error as error:
                failure = error_from_glib(error)
                _LOG.warning("%s failed: %s", method, failure.code)
                done(Err(failure))
                return
            try:
                result = parse(unpack_reply(reply))
            except (GLib.Error, TypeError, ValueError, IndexError, KeyError, AttributeError):
                _LOG.warning("%s: unreadable reply", method)
                result = Err(ServiceError(INVALID_REPLY))
            done(result)

        def send(proxy: Gio.DBusProxy | None) -> None:
            if proxy is None:
                done(Err(ServiceError(SERVICE_NOT_RUNNING)))
                return
            proxy.call(
                method,
                pack_args(signature, values),
                Gio.DBusCallFlags.NONE,
                timeout_ms,
                None,
                finished,
            )

        self.with_proxy(send)
