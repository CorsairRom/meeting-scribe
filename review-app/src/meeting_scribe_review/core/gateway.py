"""Ports the presenters talk through: the service gateway and the sample player.

Calls are asynchronous by callback: the ``ui`` adapter answers on the GLib main loop, tests answer
at once. A callback is called exactly once per call, with an ``Ok`` or an ``Err``.
"""

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from meeting_scribe_review.core.errors import ServiceError
from meeting_scribe_review.core.models import MeetingSummary, Speaker, Utterance
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
