"""``Gio.DBusProxy`` implementation of ``MeetingsGateway`` (spec section 6).

Every call is asynchronous and answers on the GLib main loop, exactly once. A call may wake the
service through bus activation. Nothing here logs a name or a transcript: a failed call logs the
method and the error code only.
"""

import logging
from collections.abc import Callable
from typing import Any

from gi.repository import Gio, GLib

from meeting_scribe_review.core import parsers
from meeting_scribe_review.core.availability import AvailabilityFilter
from meeting_scribe_review.core.constants import BUS_NAME, MEETINGS_INTERFACE, OBJECT_PATH
from meeting_scribe_review.core.errors import INVALID_REPLY, SERVICE_NOT_RUNNING, ServiceError
from meeting_scribe_review.core.gateway import Callback, GatewayListener
from meeting_scribe_review.core.models import MeetingSummary, Speaker, Utterance
from meeting_scribe_review.core.result import Err, Ok, Result
from meeting_scribe_review.core.signals import dispatch_signal
from meeting_scribe_review.ui.dbus_codec import error_from_glib, pack_args, unpack_reply

_LOG = logging.getLogger(__name__)
_CALL_TIMEOUT_MS = 30_000

type _Parser[T] = Callable[[Any], Result[T, ServiceError]]


def _bytes(value: Any) -> Result[bytes, ServiceError]:
    if isinstance(value, bytes):
        return Ok(value)
    return Err(ServiceError(INVALID_REPLY))


class DBusGateway:
    def __init__(self, bus_type: Gio.BusType = Gio.BusType.SESSION) -> None:
        self._bus_type = bus_type
        self._proxy: Gio.DBusProxy | None = None
        self._creating = False
        self._waiting: list[Callable[[Gio.DBusProxy | None], None]] = []
        self._listeners: list[GatewayListener] = []
        self._availability = AvailabilityFilter()
        self._watch_id = 0

    def list_meetings(self, done: Callback[list[MeetingSummary]]) -> None:
        self._call("List", "", (), parsers.parse_meeting_summaries, done)

    def get_speakers(self, meeting_id: str, done: Callback[list[Speaker]]) -> None:
        self._call("GetSpeakers", "s", (meeting_id,), parsers.parse_speakers, done)

    def get_transcript(self, meeting_id: str, done: Callback[list[Utterance]]) -> None:
        self._call("GetTranscript", "s", (meeting_id,), parsers.parse_transcript, done)

    def get_speaker_sample(self, meeting_id: str, speaker_id: str, done: Callback[bytes]) -> None:
        self._call("GetSpeakerSample", "ss", (meeting_id, speaker_id), _bytes, done)

    def rename_speaker(
        self, meeting_id: str, speaker_id: str, name: str, done: Callback[list[Speaker]]
    ) -> None:
        self._call(
            "RenameSpeaker", "sss", (meeting_id, speaker_id, name), parsers.parse_speakers, done
        )

    def merge_speakers(
        self, meeting_id: str, source_id: str, target_id: str, done: Callback[list[Speaker]]
    ) -> None:
        self._call(
            "MergeSpeakers", "sss", (meeting_id, source_id, target_id), parsers.parse_speakers, done
        )

    def unmerge_speaker(
        self, meeting_id: str, speaker_id: str, done: Callback[list[Speaker]]
    ) -> None:
        self._call("UnmergeSpeaker", "ss", (meeting_id, speaker_id), parsers.parse_speakers, done)

    def watch(self, listener: GatewayListener) -> None:
        """Receive the signals and the availability of the service's bus name."""
        self._listeners.append(listener)
        if self._watch_id:
            return
        self._watch_id = Gio.bus_watch_name(
            self._bus_type,
            BUS_NAME,
            Gio.BusNameWatcherFlags.NONE,
            lambda _conn, _name, _owner: self._announce(available=True),
            lambda _conn, _name: self._announce(available=False),
        )
        self._with_proxy(lambda _proxy: None)  # the proxy delivers the signals

    def close(self) -> None:
        if self._watch_id:
            Gio.bus_unwatch_name(self._watch_id)
            self._watch_id = 0
        self._listeners.clear()

    def _announce(self, *, available: bool) -> None:
        if self._availability.changed(available=available) is None:
            return
        for listener in list(self._listeners):
            listener.on_service_available(available)

    def _on_signal(
        self, _proxy: Gio.DBusProxy, _sender: str | None, name: str, parameters: GLib.Variant
    ) -> None:
        args = parameters.unpack()
        for listener in list(self._listeners):
            if not dispatch_signal(listener, name, args):
                _LOG.warning("ignored the signal %s", name)
                return

    def _with_proxy(self, then: Callable[[Gio.DBusProxy | None], None]) -> None:
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
            MEETINGS_INTERFACE,
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
            proxy.connect("g-signal", self._on_signal)
            self._proxy = proxy
        waiting, self._waiting = self._waiting, []
        for then in waiting:
            then(proxy)

    def _call[T](
        self,
        method: str,
        signature: str,
        values: tuple[Any, ...],
        parse: _Parser[T],
        done: Callback[T],
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
                _CALL_TIMEOUT_MS,
                None,
                finished,
            )

        self._with_proxy(send)
