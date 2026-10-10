from pathlib import Path

import pytest
from pydantic import ValidationError

from meeting_scribe.settings import Settings
from meeting_scribe.speakers.infrastructure.speakers_settings import SpeakersSettings

_VARS = (
    "SPEAKERS_PIPELINE",
    "SPEAKERS_NUM_SPEAKERS",
    "SPEAKERS_MIN_SPEAKERS",
    "SPEAKERS_MAX_SPEAKERS",
    "SPEAKERS_UTTERANCE_GAP_SECONDS",
    "SPEAKERS_ME_DOMINANCE_DB",
    "SPEAKERS_ME_NOISE_MARGIN_DB",
    "SPEAKERS_ME_MIN_TURN_SECONDS",
    "SPEAKERS_SAMPLE_MAX_CLIPS",
    "SPEAKERS_SAMPLE_TARGET_SECONDS",
    "SPEAKERS_SAMPLE_MIN_CLIP_SECONDS",
    "SPEAKERS_SAMPLE_MAX_CLIP_SECONDS",
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in _VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-empty"))
    monkeypatch.chdir(tmp_path)


def test_defaults_follow_the_spec() -> None:
    settings = SpeakersSettings()

    assert settings.pipeline == "pyannote/speaker-diarization-community-1"
    assert (settings.num_speakers, settings.min_speakers, settings.max_speakers) == (
        None,
        None,
        None,
    )
    assert settings.utterance_gap_seconds == 5.0
    assert (settings.me_dominance_db, settings.me_noise_margin_db) == (6.0, 10.0)
    assert settings.me_min_turn_seconds == 0.5
    assert (settings.sample_max_clips, settings.sample_target_seconds) == (3, 15.0)
    assert (settings.sample_min_clip_seconds, settings.sample_max_clip_seconds) == (1.5, 6.0)


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPEAKERS_NUM_SPEAKERS", "3")
    monkeypatch.setenv("SPEAKERS_UTTERANCE_GAP_SECONDS", "2.5")
    monkeypatch.setenv("SPEAKERS_ME_DOMINANCE_DB", "9")

    settings = SpeakersSettings()

    assert (settings.num_speakers, settings.utterance_gap_seconds) == (3, 2.5)
    assert settings.me_dominance_db == 9.0


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("SPEAKERS_PIPELINE", ""),
        ("SPEAKERS_NUM_SPEAKERS", "0"),
        ("SPEAKERS_MIN_SPEAKERS", "0"),
        ("SPEAKERS_UTTERANCE_GAP_SECONDS", "-1"),
        ("SPEAKERS_ME_NOISE_MARGIN_DB", "-1"),
        ("SPEAKERS_ME_MIN_TURN_SECONDS", "0"),
        ("SPEAKERS_SAMPLE_MAX_CLIPS", "0"),
        ("SPEAKERS_SAMPLE_TARGET_SECONDS", "0"),
        ("SPEAKERS_SAMPLE_MIN_CLIP_SECONDS", "0"),
        ("SPEAKERS_SAMPLE_MAX_CLIP_SECONDS", "0"),
        ("SPEAKERS_MAX_SPEAKERS", "0"),
        ("SPEAKERS_ME_DOMINANCE_DB", "-1"),
    ],
)
def test_invalid_values_fail_at_load(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValidationError):
        SpeakersSettings()


def test_min_may_not_exceed_max_for_speakers_or_clips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPEAKERS_MIN_SPEAKERS", "4")
    monkeypatch.setenv("SPEAKERS_MAX_SPEAKERS", "2")
    with pytest.raises(ValidationError):
        SpeakersSettings()
    monkeypatch.delenv("SPEAKERS_MIN_SPEAKERS")
    monkeypatch.delenv("SPEAKERS_MAX_SPEAKERS")
    monkeypatch.setenv("SPEAKERS_SAMPLE_MIN_CLIP_SECONDS", "7")
    with pytest.raises(ValidationError):
        SpeakersSettings()


@pytest.mark.parametrize(
    "env",
    [
        {"SPEAKERS_SAMPLE_TARGET_SECONDS": "1.0"},  # below the 1.5 s minimum clip
        {"SPEAKERS_NUM_SPEAKERS": "5", "SPEAKERS_MAX_SPEAKERS": "3"},
        {"SPEAKERS_NUM_SPEAKERS": "2", "SPEAKERS_MIN_SPEAKERS": "3"},
    ],
)
def test_contradictory_combinations_fail_at_load(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str]
) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        SpeakersSettings()


def test_an_exact_count_inside_the_bounds_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPEAKERS_NUM_SPEAKERS", "3")
    monkeypatch.setenv("SPEAKERS_MIN_SPEAKERS", "2")
    monkeypatch.setenv("SPEAKERS_MAX_SPEAKERS", "3")

    assert SpeakersSettings().num_speakers == 3


def test_root_settings_composes_speakers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPEAKERS_UTTERANCE_GAP_SECONDS", "3")

    assert Settings().speakers.utterance_gap_seconds == 3.0


def test_the_domain_params_are_snapshots_of_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPEAKERS_ME_DOMINANCE_DB", "8")
    monkeypatch.setenv("SPEAKERS_SAMPLE_MAX_CLIPS", "2")

    settings = SpeakersSettings()

    assert (settings.me_params().dominance_db, settings.me_params().min_turn_seconds) == (8.0, 0.5)
    assert settings.sample_params().max_clips == 2
    assert settings.sample_params().max_clip_seconds == 6.0
