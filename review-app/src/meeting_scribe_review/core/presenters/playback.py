"""Explicit, one-at-a-time sample playback (AC-14, AC-9).

Only ``press`` can start a sound, so opening a window, selecting a meeting or finishing an edit
never plays anything. A sample is fetched on its first press and kept in memory only, for the
window lifetime; it is dropped when the meeting changes or the window closes.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from meeting_scribe_review.core.errors import ServiceError, message_for
from meeting_scribe_review.core.gateway import MeetingsGateway, SamplePlayer
from meeting_scribe_review.core.result import Err, Result

NO_SAMPLE_CODE = "ENTITY_NOT_FOUND_SPEAKER_SAMPLE"
NO_SAMPLE_TEXT = "No sample"
CANNOT_PLAY_TEXT = "Cannot play this sample"


class PlaybackStatus(StrEnum):
    IDLE = "idle"
    LOADING = "loading"
    PLAYING = "playing"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class PlaybackState:
    loading_id: str = ""
    playing_id: str = ""
    unavailable: frozenset[str] = field(default_factory=frozenset)
    failed: frozenset[str] = field(default_factory=frozenset)
    message: str = ""
    hint: str = ""

    def status_of(self, speaker_id: str) -> PlaybackStatus:
        if speaker_id == self.playing_id:
            return PlaybackStatus.PLAYING
        if speaker_id == self.loading_id:
            return PlaybackStatus.LOADING
        if speaker_id in self.unavailable:
            return PlaybackStatus.UNAVAILABLE
        if speaker_id in self.failed:
            return PlaybackStatus.FAILED
        return PlaybackStatus.IDLE

    def text_of(self, speaker_id: str) -> str:
        """The row text for the speaker's status, empty when there is nothing to say."""
        status = self.status_of(speaker_id)
        if status is PlaybackStatus.UNAVAILABLE:
            return NO_SAMPLE_TEXT
        if status is PlaybackStatus.FAILED:
            return CANNOT_PLAY_TEXT
        return ""


class PlaybackPresenter:
    """Also the player's listener: the adapter reports the end of a sample or an error to it."""

    def __init__(
        self,
        gateway: MeetingsGateway,
        player: SamplePlayer,
        on_change: Callable[[PlaybackState], None],
    ) -> None:
        self._gateway = gateway
        self._player = player
        self._on_change = on_change
        self._state = PlaybackState()
        self._meeting_id = ""
        self._samples: dict[tuple[str, str], bytes] = {}
        self._request = 0
        player.watch(self)

    @property
    def state(self) -> PlaybackState:
        return self._state

    def press(self, meeting_id: str, speaker_id: str) -> None:
        """The play button of one speaker: start it, or stop it when it is the one playing."""
        if meeting_id != self._meeting_id:
            self.meeting_changed(meeting_id)
        if speaker_id in (self._state.playing_id, self._state.loading_id):
            self.stop()
            return
        self.stop()
        state = self._state
        self._publish(
            PlaybackState(
                loading_id=speaker_id,
                unavailable=state.unavailable - {speaker_id},
                failed=state.failed - {speaker_id},
            )
        )
        sample = self._samples.get((meeting_id, speaker_id))
        if sample is not None:
            self._play(speaker_id, sample)
            return
        self._request += 1
        request = self._request
        self._gateway.get_speaker_sample(
            meeting_id, speaker_id, lambda result: self._fetched(request, speaker_id, result)
        )

    def stop(self) -> None:
        """Stop what plays and cancel a sample still being fetched."""
        self._request += 1
        if self._state.playing_id:
            self._player.stop()
        state = self._state
        if state.playing_id or state.loading_id:
            self._publish(PlaybackState(unavailable=state.unavailable, failed=state.failed))

    def meeting_changed(self, meeting_id: str) -> None:
        self.stop()
        self._samples = {k: v for k, v in self._samples.items() if k[0] == meeting_id}
        self._meeting_id = meeting_id
        self._publish(PlaybackState())

    def forget(self, meeting_id: str) -> None:
        """Drop the kept samples of a meeting (they change when it is reprocessed)."""
        self._samples = {k: v for k, v in self._samples.items() if k[0] != meeting_id}

    def close(self) -> None:
        self.stop()
        self._samples.clear()
        self._meeting_id = ""
        self._publish(PlaybackState())

    def on_ended(self) -> None:
        if self._state.playing_id:
            self._publish(
                PlaybackState(unavailable=self._state.unavailable, failed=self._state.failed)
            )

    def on_error(self) -> None:
        state = self._state
        if state.playing_id:
            self._publish(
                PlaybackState(
                    unavailable=state.unavailable, failed=state.failed | {state.playing_id}
                )
            )

    def _fetched(self, request: int, speaker_id: str, result: Result[bytes, ServiceError]) -> None:
        if request != self._request:
            return
        if isinstance(result, Err):
            self._fetch_failed(speaker_id, result.error)
            return
        self._samples[(self._meeting_id, speaker_id)] = result.value
        self._play(speaker_id, result.value)

    def _fetch_failed(self, speaker_id: str, error: ServiceError) -> None:
        state = self._state
        if error.code == NO_SAMPLE_CODE:
            self._publish(
                PlaybackState(unavailable=state.unavailable | {speaker_id}, failed=state.failed)
            )
            return
        message = message_for(error)
        self._publish(
            PlaybackState(
                unavailable=state.unavailable,
                failed=state.failed,
                message=message.text,
                hint=message.hint,
            )
        )

    def _play(self, speaker_id: str, sample: bytes) -> None:
        state = self._state
        self._publish(
            PlaybackState(playing_id=speaker_id, unavailable=state.unavailable, failed=state.failed)
        )
        self._player.play(sample)

    def _publish(self, state: PlaybackState) -> None:
        self._state = state
        self._on_change(state)
