"""``Settings.with_overrides`` and precedence (AC-3, AC-10): one rule set, a fixed order."""

import logging
from pathlib import Path

import pytest
from returns.result import Failure, Success

from meeting_scribe.settings import Settings
from meeting_scribe.summaries.infrastructure.summaries_settings import SummariesSettings

_ENV = (
    "SUMMARIES_CLOUD_ENABLED",
    "SUMMARIES_LOCAL_MODEL",
    "SUMMARIES_PROVIDER",
    "SUMMARIES_LANGUAGE",
    "STORAGE_MEETINGS_DIR",
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.chdir(tmp_path)


def _dotenv(tmp_path: Path, content: str) -> None:
    folder = tmp_path / "xdg" / "meeting-scribe"
    folder.mkdir(parents=True)
    (folder / ".env").write_text(content, encoding="utf-8")


def test_file_values_fill_what_the_environment_did_not_set() -> None:
    settings = Settings.with_overrides(
        {"summaries.local_model": "m-file", "summaries.provider": "cloud"}
    ).unwrap()

    assert settings.summaries.local_model == "m-file"
    assert settings.summaries.provider == "cloud"


def test_precedence_and_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    key = "summaries.local_model"
    file_values = {key: "m-file"}

    default = Settings.load()
    assert (default.source_of(key).source, default.source_of(key).locked) == ("default", False)

    from_file = Settings.load(file_values=file_values)
    assert from_file.summaries.local_model == "m-file"
    assert (from_file.source_of(key).source, from_file.source_of(key).locked) == ("file", False)

    _dotenv(tmp_path, "SUMMARIES_LOCAL_MODEL=m-dotenv\n")
    from_dotenv = Settings.load(file_values=file_values)
    assert from_dotenv.summaries.local_model == "m-dotenv"
    assert (from_dotenv.source_of(key).source, from_dotenv.source_of(key).locked) == (
        "dotenv",
        True,
    )

    monkeypatch.setenv("SUMMARIES_LOCAL_MODEL", "m-env")
    from_env = Settings.load(file_values=file_values)
    assert from_env.summaries.local_model == "m-env"
    assert (from_env.source_of(key).source, from_env.source_of(key).locked) == ("env", True)


def test_explicit_init_values_still_win_over_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    """The file layer sits below the environment only for ``load``; constructors are unchanged."""
    monkeypatch.setenv("SUMMARIES_LOCAL_MODEL", "m-env")

    assert SummariesSettings(local_model="m-init").local_model == "m-init"


def test_the_file_layer_does_not_leak_into_later_loads() -> None:
    Settings.load(file_values={"summaries.local_model": "m-file"})

    assert Settings.load().summaries.local_model == "qwen3.5:9b"


def test_the_meetings_dir_comes_from_the_file_as_a_path(tmp_path: Path) -> None:
    settings = Settings.with_overrides({"storage.meetings_dir": str(tmp_path / "m")}).unwrap()

    assert settings.storage.meetings_dir == tmp_path / "m"


def test_invalid_values_are_reported_per_field_not_raised() -> None:
    result = Settings.with_overrides(
        {
            "summaries.provider": "hybrid",
            "summaries.language": "Spanish",
            "summaries.local_model": "   ",
            "summaries.cloud_model": "ok",
        }
    )

    assert isinstance(result, Failure)
    problems = {p.key: p for p in result.failure()}
    assert set(problems) == {"summaries.provider", "summaries.language", "summaries.local_model"}
    assert all(p.code == "FIELD_INVALID" for p in problems.values())
    assert "ISO" in problems["summaries.language"].message
    assert "Spanish" not in "".join(p.message for p in problems.values())


def test_a_too_long_model_is_invalid() -> None:
    result = Settings.with_overrides({"summaries.cloud_model": "m" * 201})

    assert isinstance(result, Failure)
    assert [p.key for p in result.failure()] == ["summaries.cloud_model"]


def test_an_unknown_key_is_reported() -> None:
    result = Settings.with_overrides({"summaries.cloud_base_url": "https://x.example"})

    assert isinstance(result, Failure)
    assert [(p.key, p.code) for p in result.failure()] == [
        ("summaries.cloud_base_url", "FIELD_UNKNOWN")
    ]


def test_valid_values_succeed_and_the_models_are_stripped() -> None:
    result = Settings.with_overrides({"summaries.cloud_model": "  glm-5  "})

    assert isinstance(result, Success)
    assert result.unwrap().summaries.cloud_model == "glm-5"


def test_the_service_never_starts_from_state_its_validators_reject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An invalid environment value is reported too (it cannot be fixed from the window)."""
    monkeypatch.setenv("SUMMARIES_LANGUAGE", "Not Valid")

    result = Settings.with_overrides({"summaries.provider": "cloud"})

    assert isinstance(result, Failure)
    assert [p.key for p in result.failure()] == ["summaries.language"]


def test_exposed_value_and_default_are_strings() -> None:
    settings = Settings.with_overrides({"summaries.provider": "cloud"}).unwrap()

    assert settings.exposed_value("summaries.provider") == "cloud"
    assert settings.exposed_default("summaries.provider") == "local"
    assert settings.exposed_default("storage.meetings_dir") == str(
        Path.home() / "Documents" / "meetings"
    )


# -- AC-13: the legacy SUMMARIES_CLOUD_ENABLED switch (the cloud is opt-in) --


def _legacy_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.WARNING and "SUMMARIES_CLOUD_ENABLED" in r.getMessage()
    ]


def test_a_provider_saved_in_the_window_is_the_opt_in_by_itself(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings.load(file_values={"summaries.provider": "cloud"})

    assert settings.summaries.provider == "cloud"
    assert settings.source_of("summaries.provider").lock_reason == ""
    assert _legacy_warnings(caplog) == []


@pytest.mark.parametrize("switch", [None, "maybe", "", "false"])
@pytest.mark.parametrize("layer", ["env", "dotenv"])
def test_legacy_cloud_provider_without_an_explicit_true_stays_local(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    layer: str,
    switch: str | None,
) -> None:
    lines = ["SUMMARIES_PROVIDER=cloud"]
    if switch is not None:
        lines.append(f"SUMMARIES_CLOUD_ENABLED={switch}")
    if layer == "env":
        for line in lines:
            name, value = line.split("=")
            monkeypatch.setenv(name, value)
    else:
        _dotenv(tmp_path, "\n".join(lines) + "\n")

    with caplog.at_level(logging.WARNING):
        settings = Settings.load()

    assert settings.summaries.provider == "local"
    source = settings.source_of("summaries.provider")
    assert (source.source, source.locked, source.lock_reason) == (layer, True, "LEGACY_SETTING")
    warnings = _legacy_warnings(caplog)
    assert len(warnings) == 1
    assert "=cloud" not in warnings[0]  # the variable is named, never a value


def test_legacy_false_overrides_a_saved_cloud_too(tmp_path: Path) -> None:
    _dotenv(tmp_path, "SUMMARIES_CLOUD_ENABLED=false\n")

    settings = Settings.load(file_values={"summaries.provider": "cloud"})

    assert settings.summaries.provider == "local"
    assert settings.source_of("summaries.provider").source == "dotenv"
    assert settings.source_of("summaries.provider").lock_reason == "LEGACY_SETTING"


def test_legacy_true_with_the_cloud_provider_keeps_the_cloud(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("SUMMARIES_PROVIDER", "cloud")
    monkeypatch.setenv("SUMMARIES_CLOUD_ENABLED", "true")

    with caplog.at_level(logging.WARNING):
        settings = Settings.load()

    assert settings.summaries.provider == "cloud"
    assert settings.source_of("summaries.provider").locked is True
    assert settings.source_of("summaries.provider").lock_reason == ""
    assert len(_legacy_warnings(caplog)) == 1


def test_legacy_true_with_the_local_provider_says_the_per_request_cloud_is_gone(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("SUMMARIES_CLOUD_ENABLED", "true")

    with caplog.at_level(logging.WARNING):
        settings = Settings.load()

    assert settings.summaries.provider == "local"
    assert settings.source_of("summaries.provider").locked is False
    (warning,) = _legacy_warnings(caplog)
    assert "obsolete" in warning
    assert "per-request" in warning
    assert "Preferences" in warning


def test_with_overrides_does_not_repeat_the_legacy_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("SUMMARIES_CLOUD_ENABLED", "true")

    with caplog.at_level(logging.WARNING):
        Settings.with_overrides({"summaries.language": "es"}).unwrap()

    assert _legacy_warnings(caplog) == []


def test_file_values_outside_the_registry_never_break_a_load(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        settings = Settings.load(
            file_values={
                "summaries": "x",
                "summaries.bogus": "y",
                "summaries.local_model": "m-ok",
                "log_level.x": "z",
            }
        )

    assert settings.summaries.local_model == "m-ok"
    assert "summaries.bogus" in caplog.text
    assert "bogus" in caplog.text and "=y" not in caplog.text
