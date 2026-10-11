"""Playback is explicit and one at a time; tests use a fake player, never a device (AC-14, AC-9)."""

from meeting_scribe_review.core.errors import SERVICE_NOT_RUNNING
from meeting_scribe_review.core.presenters.playback import (
    PlaybackPresenter,
    PlaybackState,
    PlaybackStatus,
)
from meeting_scribe_review.core.result import Ok
from tests.unit.review_app.fakes import FakeGateway, FakePlayer


def _rig(
    *, deferred: bool = False
) -> tuple[PlaybackPresenter, FakeGateway, FakePlayer, list[PlaybackState]]:
    gateway = FakeGateway(deferred=deferred)
    gateway.replies["get_speaker_sample"] = lambda _meeting, speaker: Ok(f"WAV-{speaker}".encode())
    player = FakePlayer()
    seen: list[PlaybackState] = []
    return PlaybackPresenter(gateway, player, seen.append), gateway, player, seen


def test_nothing_plays_until_the_user_presses_play() -> None:
    presenter, gateway, player, _ = _rig()
    presenter.meeting_changed("m1")
    assert player.events == [] and gateway.calls == []
    assert presenter.state.status_of("S1") is PlaybackStatus.IDLE


def test_pressing_play_fetches_the_sample_and_plays_it() -> None:
    presenter, gateway, player, _ = _rig()
    presenter.press("m1", "S1")
    assert gateway.names_of("get_speaker_sample") == [("m1", "S1")]
    assert player.played == [b"WAV-S1"]
    assert presenter.state.status_of("S1") is PlaybackStatus.PLAYING


def test_the_row_shows_loading_while_the_sample_is_fetched() -> None:
    presenter, gateway, player, _ = _rig(deferred=True)
    presenter.press("m1", "S1")
    assert presenter.state.status_of("S1") is PlaybackStatus.LOADING
    gateway.release()
    assert presenter.state.status_of("S1") is PlaybackStatus.PLAYING and player.played


def test_pressing_another_speaker_stops_the_first_and_plays_the_second() -> None:
    presenter, _, player, _ = _rig()
    presenter.press("m1", "S1")
    presenter.press("m1", "S2")
    assert [kind for kind, _ in player.events] == ["play", "stop", "play"]
    assert player.played == [b"WAV-S1", b"WAV-S2"]
    assert presenter.state.status_of("S1") is PlaybackStatus.IDLE
    assert presenter.state.status_of("S2") is PlaybackStatus.PLAYING


def test_pressing_the_playing_speaker_stops_playback() -> None:
    presenter, _, player, _ = _rig()
    presenter.press("m1", "S1")
    presenter.press("m1", "S1")
    assert player.events[-1] == ("stop", None) and len(player.played) == 1
    assert presenter.state.status_of("S1") is PlaybackStatus.IDLE


def test_a_second_press_reuses_the_sample_kept_in_memory() -> None:
    presenter, gateway, player, _ = _rig()
    presenter.press("m1", "S1")
    presenter.press("m1", "S1")  # stop
    presenter.press("m1", "S1")
    assert len(gateway.names_of("get_speaker_sample")) == 1
    assert len(player.played) == 2


def test_a_reply_that_arrives_after_another_press_is_dropped() -> None:
    presenter, gateway, player, _ = _rig(deferred=True)
    presenter.press("m1", "S1")
    presenter.press("m1", "S2")
    gateway.release(0)  # S1 arrives late
    assert player.played == []
    gateway.release(0)
    assert player.played == [b"WAV-S2"]


def test_pressing_a_loading_speaker_cancels_it() -> None:
    presenter, gateway, player, _ = _rig(deferred=True)
    presenter.press("m1", "S1")
    presenter.press("m1", "S1")
    gateway.release()
    assert player.played == [] and presenter.state.status_of("S1") is PlaybackStatus.IDLE


def test_the_end_of_a_sample_returns_the_row_to_idle() -> None:
    presenter, _, player, _ = _rig()
    presenter.press("m1", "S1")
    player.finish()
    assert presenter.state.status_of("S1") is PlaybackStatus.IDLE


def test_a_backend_error_marks_the_row_and_a_press_retries() -> None:
    presenter, _, player, _ = _rig()
    presenter.press("m1", "S1")
    player.fail()
    assert presenter.state.status_of("S1") is PlaybackStatus.FAILED
    assert presenter.state.text_of("S1") == "Cannot play this sample"
    presenter.press("m1", "S1")
    assert presenter.state.status_of("S1") is PlaybackStatus.PLAYING


def test_a_missing_sample_shows_no_sample() -> None:
    presenter, gateway, player, _ = _rig()
    gateway.fail("get_speaker_sample", "ENTITY_NOT_FOUND_SPEAKER_SAMPLE")
    presenter.press("m1", "S4")
    assert presenter.state.status_of("S4") is PlaybackStatus.UNAVAILABLE
    assert presenter.state.text_of("S4") == "No sample"
    assert player.events == []


def test_another_failure_shows_its_message_and_plays_nothing() -> None:
    presenter, gateway, player, _ = _rig()
    gateway.fail("get_speaker_sample", SERVICE_NOT_RUNNING)
    presenter.press("m1", "S1")
    assert presenter.state.message == "The Meeting Scribe service is not running"
    assert presenter.state.status_of("S1") is PlaybackStatus.IDLE and player.events == []


def test_switching_meeting_stops_playback_and_forgets_the_other_samples() -> None:
    presenter, gateway, player, _ = _rig()
    presenter.press("m1", "S1")
    presenter.meeting_changed("m2")
    assert player.events[-1] == ("stop", None)
    assert presenter.state.status_of("S1") is PlaybackStatus.IDLE
    presenter.press("m1", "S1")
    assert len(gateway.names_of("get_speaker_sample")) == 2


def test_pressing_in_another_meeting_stops_the_old_one_first() -> None:
    presenter, _, player, _ = _rig()
    presenter.press("m1", "S1")
    presenter.press("m2", "S1")
    assert [kind for kind, _ in player.events] == ["play", "stop", "play"]


def test_closing_the_window_stops_playback_and_drops_the_samples() -> None:
    presenter, gateway, player, _ = _rig()
    presenter.press("m1", "S1")
    presenter.close()
    assert player.events[-1] == ("stop", None)
    presenter.press("m1", "S1")
    assert len(gateway.names_of("get_speaker_sample")) == 2


def test_closing_while_idle_does_not_touch_the_player() -> None:
    presenter, _, player, _ = _rig()
    presenter.close()
    assert player.events == []


def test_forgetting_a_meeting_drops_its_samples_after_a_reprocess() -> None:
    presenter, gateway, _, _ = _rig()
    presenter.press("m1", "S1")
    presenter.stop()
    presenter.forget("m1")
    presenter.press("m1", "S1")
    assert len(gateway.names_of("get_speaker_sample")) == 2
