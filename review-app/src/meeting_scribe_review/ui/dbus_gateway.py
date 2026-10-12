"""``Gio.DBusProxy`` implementation of ``MeetingsGateway`` and ``SummariesGateway`` (spec 6).

Each interface has its own proxy (``InterfaceProxy``); calls are asynchronous and answer on the
GLib main loop, exactly once. Nothing here logs a name, a transcript or a summary.
"""

import logging
from collections.abc import Callable
from typing import Any

from gi.repository import Gio

from meeting_scribe_review.core import parsers
from meeting_scribe_review.core.availability import AvailabilityFilter
from meeting_scribe_review.core.constants import (
    BUS_NAME,
    MEETINGS_INTERFACE,
    PROPERTIES_INTERFACE,
    SETTINGS_INTERFACE,
    SUMMARIES_INTERFACE,
)
from meeting_scribe_review.core.errors import INVALID_REPLY, ServiceError
from meeting_scribe_review.core.gateway import (
    Callback,
    GatewayListener,
    SettingsListener,
    SummaryListener,
)
from meeting_scribe_review.core.models import (
    MeetingSummary,
    ProviderRow,
    SetOutcome,
    SettingsField,
    Speaker,
    SummaryDocument,
    SummaryInfo,
    Utterance,
)
from meeting_scribe_review.core.result import Err, Ok, Result
from meeting_scribe_review.core.signals import (
    dispatch_settings_signal,
    dispatch_signal,
    dispatch_summary_signal,
)
from meeting_scribe_review.ui.dbus_proxy import InterfaceProxy

_LOG = logging.getLogger(__name__)

type _Parser[T] = Callable[[Any], Result[T, ServiceError]]


def _bytes(value: Any) -> Result[bytes, ServiceError]:
    if isinstance(value, bytes):
        return Ok(value)
    return Err(ServiceError(INVALID_REPLY))


class DBusGateway:
    def __init__(self, bus_type: Gio.BusType = Gio.BusType.SESSION) -> None:
        self._bus_type = bus_type
        self._meetings = InterfaceProxy(bus_type, MEETINGS_INTERFACE, self._on_signal)
        self._summaries = InterfaceProxy(bus_type, SUMMARIES_INTERFACE, self._on_summary_signal)
        self._settings = InterfaceProxy(bus_type, SETTINGS_INTERFACE, self._on_settings_signal)
        self._properties = InterfaceProxy(bus_type, PROPERTIES_INTERFACE, lambda _n, _a: None)
        self._listeners: list[GatewayListener] = []
        self._settings_listeners: list[SettingsListener] = []
        self._summary_listeners: list[SummaryListener] = []
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
        self._watch_name()
        self._meetings.with_proxy(lambda _proxy: None)  # the proxy delivers the signals

    def _watch_name(self) -> None:
        if self._watch_id:
            return
        self._watch_id = Gio.bus_watch_name(
            self._bus_type,
            BUS_NAME,
            Gio.BusNameWatcherFlags.NONE,
            lambda _conn, _name, _owner: self._announce(available=True),
            lambda _conn, _name: self._announce(available=False),
        )

    def get_all(self, done: Callback[list[SettingsField]]) -> None:
        self._settings.call("GetAll", "", (), parsers.parse_settings_fields, done)

    def set_values(self, values: dict[str, str], done: Callback[SetOutcome]) -> None:
        self._settings.call("Set", "a{ss}", (values,), parsers.parse_set_outcome, done)

    def restart_required(self, done: Callback[tuple[str, ...]]) -> None:
        self._properties.call(
            "Get",
            "ss",
            (SETTINGS_INTERFACE, "RestartRequired"),
            parsers.parse_keys,
            done,
        )

    def restart(self, done: Callback[str]) -> None:
        self._settings.call("Restart", "", (), parsers.parse_restarting, done)

    def watch_settings(self, listener: SettingsListener) -> None:
        """Receive ``SettingsChanged`` and the availability of the service's bus name."""
        self._settings_listeners.append(listener)
        self._watch_name()
        self._settings.with_proxy(lambda _proxy: None)
        known = self._availability.known
        if known is not None:  # the baseline was reported before this listener existed
            listener.on_service_available(known)

    def unwatch_settings(self, listener: SettingsListener) -> None:
        if listener in self._settings_listeners:
            self._settings_listeners.remove(listener)

    def providers(self, done: Callback[list[ProviderRow]]) -> None:
        self._summaries.call("Providers", "", (), parsers.parse_providers, done)

    def summarize(
        self, meeting_id: str, provider_id: str, done: Callback[str], *, cloud_confirmed: bool
    ) -> None:
        self._summaries.call(
            "Summarize",
            "ssb",
            (meeting_id, provider_id, cloud_confirmed),
            parsers.parse_started,
            done,
        )

    def cancel_summary(self, meeting_id: str, done: Callback[bool]) -> None:
        self._summaries.call("CancelSummary", "s", (meeting_id,), parsers.parse_cancelled, done)

    def get_summary_status(self, meeting_id: str, done: Callback[SummaryInfo]) -> None:
        self._summaries.call(
            "GetSummaryStatus", "s", (meeting_id,), parsers.parse_summary_info, done
        )

    def get_summary(self, meeting_id: str, done: Callback[SummaryDocument]) -> None:
        self._summaries.call("GetSummary", "s", (meeting_id,), parsers.parse_summary_document, done)

    def watch_summaries(self, listener: SummaryListener) -> None:
        """Receive the ``Summaries1`` signals; creating the proxy does not wake the service."""
        self._summary_listeners.append(listener)
        self._summaries.with_proxy(lambda _proxy: None)

    def close(self) -> None:
        if self._watch_id:
            Gio.bus_unwatch_name(self._watch_id)
            self._watch_id = 0
        self._listeners.clear()
        self._summary_listeners.clear()
        self._settings_listeners.clear()

    def _announce(self, *, available: bool) -> None:
        if self._availability.changed(available=available) is None:
            return
        for listener in [*self._listeners, *self._settings_listeners]:
            listener.on_service_available(available)

    def _on_settings_signal(self, name: str, args: Any) -> None:
        for listener in list(self._settings_listeners):
            if not dispatch_settings_signal(listener, name, args):
                _LOG.warning("ignored the signal %s", name)
                return

    def _on_signal(self, name: str, args: Any) -> None:
        for listener in list(self._listeners):
            if not dispatch_signal(listener, name, args):
                _LOG.warning("ignored the signal %s", name)
                return

    def _on_summary_signal(self, name: str, args: Any) -> None:
        for listener in list(self._summary_listeners):
            if not dispatch_summary_signal(listener, name, args):
                _LOG.warning("ignored the signal %s", name)
                return

    def _call[T](
        self,
        method: str,
        signature: str,
        values: tuple[Any, ...],
        parse: _Parser[T],
        done: Callback[T],
    ) -> None:
        self._meetings.call(method, signature, values, parse, done)
