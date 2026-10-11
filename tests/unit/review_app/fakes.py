"""Fakes for the app ports: a scripted gateway with synchronous callbacks and a recording player.

Nothing here touches D-Bus, GTK or an audio device (BR-001).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from meeting_scribe_review.core.errors import ServiceError
from meeting_scribe_review.core.gateway import (
    Callback,
    GatewayListener,
    PlayerListener,
    SummaryListener,
)
from meeting_scribe_review.core.models import (
    MeetingSummary,
    ProviderRow,
    Speaker,
    SummaryDocument,
    SummaryInfo,
    Utterance,
)
from meeting_scribe_review.core.presenters.cloud_confirmation import ConfirmationState
from meeting_scribe_review.core.presenters.meeting_list import MeetingListState
from meeting_scribe_review.core.presenters.merge import MergeState
from meeting_scribe_review.core.presenters.playback import PlaybackState
from meeting_scribe_review.core.presenters.rename import RenameState
from meeting_scribe_review.core.presenters.review import ReviewState
from meeting_scribe_review.core.presenters.summary import SummaryState
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
    deferred: bool = False
    pending: list[Callable[[], None]] = field(default_factory=list)

    def release(self, index: int = 0) -> None:
        """Deliver one held reply (``deferred`` mode), to test replies that arrive late."""
        self.pending.pop(index)()

    def _answer[T](self, method: str, args: tuple[Any, ...], default: T, done: Callback[T]) -> None:
        self.calls.append((method, args))
        reply = self.replies.get(method, Ok(default))
        result = reply(*args) if callable(reply) else reply
        if self.deferred:
            self.pending.append(lambda: done(result))
        else:
            done(result)

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

    def args_of(self, method: str) -> list[tuple[Any, ...]]:
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


@dataclass
class FakeViews:
    """Records every state the session shows."""

    meetings: list[MeetingListState] = field(default_factory=list)
    reviews: list[ReviewState] = field(default_factory=list)
    renames: list[RenameState] = field(default_factory=list)
    merges: list[MergeState] = field(default_factory=list)
    playbacks: list[PlaybackState] = field(default_factory=list)
    summaries: list[SummaryState] = field(default_factory=list)
    confirmations: list[ConfirmationState] = field(default_factory=list)

    def show_meetings(self, state: MeetingListState) -> None:
        self.meetings.append(state)

    def show_review(self, state: ReviewState) -> None:
        self.reviews.append(state)

    def show_rename(self, state: RenameState) -> None:
        self.renames.append(state)

    def show_merge(self, state: MergeState) -> None:
        self.merges.append(state)

    def show_playback(self, state: PlaybackState) -> None:
        self.playbacks.append(state)

    def show_summary(self, state: SummaryState) -> None:
        self.summaries.append(state)

    def show_cloud_confirmation(self, state: ConfirmationState) -> None:
        self.confirmations.append(state)


def provider(
    provider_id: str = "local",
    *,
    label: str | None = None,
    cloud: bool = False,
    available: bool = True,
    reason: str = "",
    default: bool = True,
) -> ProviderRow:
    shown = label or ("cloud: Fake Gateway, fake-model" if cloud else "Local (fake-model)")
    return ProviderRow(provider_id, shown, cloud, "fake-model", available, reason, default)


def summary_info(
    state: str = "NONE",
    *,
    generated: bool = False,
    stale: bool = False,
    cloud: bool = False,
    step: int = 0,
    total_steps: int = 0,
    failure_code: str = "",
    day: int = 9,
) -> SummaryInfo:
    return SummaryInfo(
        state=state,
        stale=stale,
        cloud=cloud,
        provider_label="cloud: Fake Gateway, fake-model" if cloud else "Local (fake-model)",
        generated_at=datetime(2026, 10, day, 11, 0, tzinfo=UTC) if generated else None,
        step=step,
        total_steps=total_steps,
        failure_code=failure_code,
    )


def summary_document(*, cloud: bool = False, stale: bool = False, day: int = 9) -> SummaryDocument:
    return SummaryDocument(
        markdown="# Fake title\n\nFake body.",
        title="Fake title",
        generated_at=datetime(2026, 10, day, 11, 0, tzinfo=UTC),
        provider_label="cloud: Fake Gateway, fake-model" if cloud else "Local (fake-model)",
        cloud=cloud,
        model="fake-model",
        language="en",
        stale=stale,
    )


@dataclass
class FakeSummariesGateway:
    """Scripted ``Summaries1``; callbacks are synchronous unless ``deferred``. Sends nothing."""

    replies: dict[str, Any] = field(default_factory=dict)
    calls: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)
    listeners: list[SummaryListener] = field(default_factory=list)
    deferred: bool = False
    pending: list[Callable[[], None]] = field(default_factory=list)

    def release(self, index: int = 0) -> None:
        self.pending.pop(index)()

    def _answer[T](self, method: str, args: tuple[Any, ...], default: T, done: Callback[T]) -> None:
        self.calls.append((method, args))
        reply = self.replies.get(method, Ok(default))
        result = reply(*args) if callable(reply) else reply
        if self.deferred:
            self.pending.append(lambda: done(result))
        else:
            done(result)

    def fail(self, method: str, code: str) -> None:
        self.replies[method] = Err(ServiceError(code))

    def providers(self, done: Callback[list[ProviderRow]]) -> None:
        self._answer("providers", (), [provider()], done)

    def summarize(
        self, meeting_id: str, provider_id: str, done: Callback[str], *, cloud_confirmed: bool
    ) -> None:
        self._answer("summarize", (meeting_id, provider_id, cloud_confirmed), "RUNNING", done)

    def cancel_summary(self, meeting_id: str, done: Callback[bool]) -> None:
        self._answer("cancel_summary", (meeting_id,), True, done)

    def get_summary_status(self, meeting_id: str, done: Callback[SummaryInfo]) -> None:
        self._answer("get_summary_status", (meeting_id,), summary_info(), done)

    def get_summary(self, meeting_id: str, done: Callback[SummaryDocument]) -> None:
        self._answer("get_summary", (meeting_id,), summary_document(), done)

    def watch_summaries(self, listener: SummaryListener) -> None:
        self.listeners.append(listener)

    def args_of(self, method: str) -> list[tuple[Any, ...]]:
        return [args for name, args in self.calls if name == method]
