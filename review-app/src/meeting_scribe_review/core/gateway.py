"""Ports the presenters talk through: the service gateway and the sample player.

Calls are asynchronous by callback: the ``ui`` adapter answers on the GLib main loop, tests answer
at once. A callback is called exactly once per call, with an ``Ok`` or an ``Err``.
"""

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from meeting_scribe_review.core.errors import ServiceError
from meeting_scribe_review.core.models import (
    KeyStatus,
    MeetingSummary,
    ProviderRow,
    SetOutcome,
    SettingsField,
    Speaker,
    SummaryDocument,
    SummaryInfo,
    Utterance,
)
from meeting_scribe_review.core.result import Result

type Callback[T] = Callable[[Result[T, ServiceError]], None]


class GatewayListener(Protocol):
    """What the service announces: signals and the availability of its bus name."""

    def on_service_available(self, available: bool) -> None: ...

    def on_processing_state_changed(self, meeting_id: str, state: str) -> None: ...

    def on_processing_finished(self, meeting_id: str, state: str, speaker_count: int) -> None: ...

    def on_speakers_changed(self, meeting_id: str) -> None: ...


@runtime_checkable
class MeetingsGateway(Protocol):
    """The ``Meetings1`` calls the app makes. Never exposes a path (ADR-005)."""

    def list_meetings(self, done: Callback[list[MeetingSummary]]) -> None: ...

    def get_speakers(self, meeting_id: str, done: Callback[list[Speaker]]) -> None: ...

    def get_transcript(self, meeting_id: str, done: Callback[list[Utterance]]) -> None: ...

    def get_speaker_sample(
        self, meeting_id: str, speaker_id: str, done: Callback[bytes]
    ) -> None: ...

    def rename_speaker(
        self, meeting_id: str, speaker_id: str, name: str, done: Callback[list[Speaker]]
    ) -> None: ...

    def merge_speakers(
        self, meeting_id: str, source_id: str, target_id: str, done: Callback[list[Speaker]]
    ) -> None: ...

    def unmerge_speaker(
        self, meeting_id: str, speaker_id: str, done: Callback[list[Speaker]]
    ) -> None: ...

    def watch(self, listener: GatewayListener) -> None: ...


class SummaryListener(Protocol):
    """What ``Summaries1`` announces while a summary runs and when it ends."""

    def on_summary_progress(self, meeting_id: str, step: int, total_steps: int) -> None: ...

    def on_summary_finished(self, meeting_id: str, state: str, error_code: str) -> None: ...


@runtime_checkable
class SummariesGateway(Protocol):
    """The ``Summaries1`` calls the app makes. Never exposes a path, a URL or a key (ADR-005)."""

    def providers(self, done: Callback[list[ProviderRow]]) -> None: ...

    def summarize(
        self,
        meeting_id: str,
        provider_id: str,
        done: Callback[str],
        *,
        cloud_confirmed: bool,
    ) -> None: ...

    def cancel_summary(self, meeting_id: str, done: Callback[bool]) -> None: ...

    def get_summary_status(self, meeting_id: str, done: Callback[SummaryInfo]) -> None: ...

    def get_summary(self, meeting_id: str, done: Callback[SummaryDocument]) -> None: ...

    def watch_summaries(self, listener: SummaryListener) -> None: ...


class SettingsListener(Protocol):
    """What ``Settings1`` announces, plus the availability of the service's bus name."""

    def on_service_available(self, available: bool) -> None: ...

    def on_settings_changed(self, keys: tuple[str, ...]) -> None: ...


@runtime_checkable
class SettingsGateway(Protocol):
    """The ``Settings1`` calls the app makes. The API key is not a setting and never passes here."""

    def get_all(self, done: Callback[list[SettingsField]]) -> None: ...

    def set_values(self, values: dict[str, str], done: Callback[SetOutcome]) -> None: ...

    def restart_required(self, done: Callback[tuple[str, ...]]) -> None: ...

    def restart(self, done: Callback[str]) -> None: ...

    def watch_settings(self, listener: SettingsListener) -> None: ...


@runtime_checkable
class ApiKeyStore(Protocol):
    """Stores the cloud API key in the GNOME Keyring (BR-005). The key never crosses the service."""

    def store(self, secret: str, done: Callback[None]) -> None: ...

    def is_stored(self, done: Callback[KeyStatus]) -> None:
        """Whether the item exists, found without reading the secret (it never reveals the key)."""
        ...

    def remove(self, done: Callback[None]) -> None: ...


class PlayerListener(Protocol):
    """Backend events: the sample ended, or playback failed (the adapter logs domain and code)."""

    def on_ended(self) -> None: ...

    def on_error(self) -> None: ...


@runtime_checkable
class SamplePlayer(Protocol):
    """Plays one WAV sample at a time from memory. Only a user press may call ``play``."""

    def play(self, data: bytes) -> None: ...

    def stop(self) -> None: ...

    def watch(self, listener: PlayerListener) -> None: ...
