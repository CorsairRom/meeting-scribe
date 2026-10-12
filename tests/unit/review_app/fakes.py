"""Fakes for the app ports: a scripted gateway with synchronous callbacks and a recording player.

Nothing here touches D-Bus, GTK or an audio device (BR-001).
"""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from meeting_scribe_review.core.errors import ServiceError
from meeting_scribe_review.core.gateway import (
    Callback,
    GatewayListener,
    PlayerListener,
    SettingsListener,
    SummaryListener,
)
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
from meeting_scribe_review.core.presenters.api_key import ApiKeyState
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
    api_keys: list[ApiKeyState] = field(default_factory=list)

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

    def show_api_key(self, state: ApiKeyState) -> None:
        self.api_keys.append(state)


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


@dataclass
class FakeApiKeyStore:
    """Records what would be stored in the keyring (never the real one); ``result`` is scripted."""

    result: Any = None
    stored: list[str] = field(default_factory=list)
    status: Any = (
        None  # a scripted ``Ok(KeyStatus)`` or ``Err``; default: the key follows ``stored``
    )
    removed: int = 0
    remove_result: Any = None

    def store(self, secret: str, done: Callback[None]) -> None:
        self.stored.append(secret)
        done(self.result if self.result is not None else Ok(None))

    def is_stored(self, done: Callback[KeyStatus]) -> None:
        if self.status is not None:
            done(self.status)
        else:
            done(Ok(KeyStatus.STORED if self.stored else KeyStatus.MISSING))

    def remove(self, done: Callback[None]) -> None:
        self.removed += 1
        result = self.remove_result if self.remove_result is not None else Ok(None)
        if isinstance(result, Ok):
            self.stored.clear()
        done(result)


def setting(  # noqa: PLR0913
    key: str,
    value: str,
    *,
    kind: str = "text",
    default: str = "",
    source: str = "default",
    locked: bool = False,
    lock_reason: str = "",
    apply: str = "live",
    choices: tuple[str, ...] = (),
    file_problem: str = "",
    apply_problem: str = "",
) -> SettingsField:
    return SettingsField(
        key, kind, value, default or value, source, locked, lock_reason, apply, choices,
        file_problem, apply_problem,
    )  # fmt: skip


def default_settings() -> list[SettingsField]:
    """The registry as the service answers it with nothing saved (the local provider)."""
    return [
        setting("summaries.provider", "local", kind="choice", choices=("local", "cloud")),
        setting("summaries.local_model", "fake-local-model"),
        setting("summaries.language", "auto", kind="choice", choices=("auto",)),
        setting("summaries.cloud_model", "fake-cloud-model"),
        setting("storage.meetings_dir", "/fake/meetings", kind="path", apply="restart"),
    ]


@dataclass
class FakeSettingsGateway:
    """Scripted ``Settings1``: ``fields`` is the saved state; ``set_values`` applies it for real."""

    fields: list[SettingsField] = field(default_factory=default_settings)
    calls: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)
    replies: dict[str, Any] = field(default_factory=dict)  # method -> a scripted Ok or Err
    pending: tuple[str, ...] = ()
    errors: tuple[Any, ...] = ()  # FieldIssue rows the next set_values returns
    listeners: list[SettingsListener] = field(default_factory=list)

    def get_all(self, done: Callback[list[SettingsField]]) -> None:
        self.calls.append(("get_all", ()))
        done(self.replies.get("get_all", Ok(list(self.fields))))

    def set_values(self, values: dict[str, str], done: Callback[SetOutcome]) -> None:
        self.calls.append(("set_values", (dict(values),)))
        if "set_values" in self.replies:
            done(self.replies["set_values"])
            return
        if self.errors:
            done(Ok(SetOutcome((), self.pending, False, self.errors)))
            return
        self.fields = [
            replace(
                f, value=values.get(f.key, f.value), source="file" if f.key in values else f.source
            )
            for f in self.fields
        ]
        done(Ok(SetOutcome(tuple(values), self.pending, False, ())))

    def restart_required(self, done: Callback[tuple[str, ...]]) -> None:
        self.calls.append(("restart_required", ()))
        done(self.replies.get("restart_required", Ok(self.pending)))

    def restart(self, done: Callback[str]) -> None:
        self.calls.append(("restart", ()))
        done(self.replies.get("restart", Ok("RESTARTING")))

    def watch_settings(self, listener: SettingsListener) -> None:
        self.listeners.append(listener)

    def args_of(self, method: str) -> list[tuple[Any, ...]]:
        return [args for name, args in self.calls if name == method]
