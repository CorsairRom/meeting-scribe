"""Fakes for the app ports: a scripted gateway with synchronous callbacks and a recording player.

Nothing here touches D-Bus, GTK or an audio device (BR-001).
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from meeting_scribe_review.core.errors import ServiceError
from meeting_scribe_review.core.gateway import (
    Callback,
    GatewayListener,
    PlayerListener,
)
from meeting_scribe_review.core.models import MeetingSummary, Speaker, Utterance
from meeting_scribe_review.core.result import Err, Ok


def speaker(
    speaker_id: str,
    *,
    name: str = "",
    label: str | None = None,
    is_me: bool = False,
    has_sample: bool = True,
    merged_into: str = "",
    talk_seconds: float = 10.0,
    utterance_count: int = 2,
) -> Speaker:
    shown = label or ("Me" if is_me else f"Speaker {speaker_id.removeprefix('S')}")
    return Speaker(
        id=speaker_id,
        label=shown,
        name=name,
        display=name or shown,
        is_me=is_me,
        talk_seconds=talk_seconds,
        utterance_count=utterance_count,
        has_sample=has_sample,
        merged_into=merged_into,
    )


@dataclass
class FakeGateway:
    """``replies[method]`` is a ``Result`` (or a callable of the call arguments returning one)."""

    replies: dict[str, Any] = field(default_factory=dict)
    calls: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)
    listeners: list[GatewayListener] = field(default_factory=list)

    def _answer[T](self, method: str, args: tuple[Any, ...], default: T, done: Callback[T]) -> None:
        self.calls.append((method, args))
        reply = self.replies.get(method, Ok(default))
        done(reply(*args) if callable(reply) else reply)

    def fail(self, method: str, code: str) -> None:
        self.replies[method] = Err(ServiceError(code))

    def list_meetings(self, done: Callback[list[MeetingSummary]]) -> None:
        self._answer("list_meetings", (), [], done)

    def get_speakers(self, meeting_id: str, done: Callback[list[Speaker]]) -> None:
        self._answer("get_speakers", (meeting_id,), [], done)

    def get_transcript(self, meeting_id: str, done: Callback[list[Utterance]]) -> None:
        self._answer("get_transcript", (meeting_id,), [], done)

    def get_speaker_sample(self, meeting_id: str, speaker_id: str, done: Callback[bytes]) -> None:
        self._answer("get_speaker_sample", (meeting_id, speaker_id), b"RIFFfake", done)

    def rename_speaker(
        self, meeting_id: str, speaker_id: str, name: str, done: Callback[list[Speaker]]
    ) -> None:
        self._answer("rename_speaker", (meeting_id, speaker_id, name), [], done)

    def merge_speakers(
        self, meeting_id: str, source_id: str, target_id: str, done: Callback[list[Speaker]]
    ) -> None:
        self._answer("merge_speakers", (meeting_id, source_id, target_id), [], done)

    def unmerge_speaker(
        self, meeting_id: str, speaker_id: str, done: Callback[list[Speaker]]
    ) -> None:
        self._answer("unmerge_speaker", (meeting_id, speaker_id), [], done)

    def watch(self, listener: GatewayListener) -> None:
        self.listeners.append(listener)

    def names_of(self, method: str) -> list[tuple[Any, ...]]:
        return [args for name, args in self.calls if name == method]


@dataclass
class FakePlayer:
    """Records what would be played; ``finish`` and ``fail`` simulate the backend callbacks."""

    events: list[tuple[str, bytes | None]] = field(default_factory=list)
    listeners: list[PlayerListener] = field(default_factory=list)

    def play(self, data: bytes) -> None:
        self.events.append(("play", data))

    def stop(self) -> None:
        self.events.append(("stop", None))

    def watch(self, listener: PlayerListener) -> None:
        self.listeners.append(listener)

    def finish(self) -> None:
        for listener in self.listeners:
            listener.on_ended()

    def fail(self) -> None:
        for listener in self.listeners:
            listener.on_error()

    @property
    def played(self) -> list[bytes]:
        return [data for kind, data in self.events if kind == "play" and data is not None]


def meeting(
    meeting_id: str, state: str = "DONE", *, day: int = 9, speakers: int = 2
) -> MeetingSummary:
    return MeetingSummary(
        id=meeting_id,
        started_at=datetime(2026, 10, day, 10, 15, tzinfo=UTC),
        duration_seconds=754.0,
        state=state,
        active_speaker_count=speakers,
    )
