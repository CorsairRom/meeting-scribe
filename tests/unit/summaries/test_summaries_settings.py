from pathlib import Path

import pytest
from pydantic import ValidationError

from meeting_scribe.settings import Settings
from meeting_scribe.summaries.infrastructure.summaries_settings import (
    CLOUD_PROVIDERS,
    DEFAULT_CLOUD_PROVIDER,
    SummariesSettings,
)

_VARS = (
    "SUMMARIES_PROVIDER",
    "SUMMARIES_LOCAL_BASE_URL",
    "SUMMARIES_LOCAL_MODEL",
    "SUMMARIES_LOCAL_CONTEXT_TOKENS",
    "SUMMARIES_UNLOAD_LOCAL_MODEL",
    "SUMMARIES_CLOUD_ENABLED",
    "SUMMARIES_CLOUD_PROVIDER",
    "SUMMARIES_CLOUD_PROVIDER_NAME",
    "SUMMARIES_CLOUD_BASE_URL",
    "SUMMARIES_CLOUD_MODEL",
    "SUMMARIES_CLOUD_CONTEXT_TOKENS",
    "SUMMARIES_LANGUAGE",
    "SUMMARIES_MAX_OUTPUT_TOKENS",
    "SUMMARIES_TEMPERATURE",
    "SUMMARIES_TIMEOUT_SECONDS",
    "SUMMARIES_TOTAL_TIMEOUT_SECONDS",
    "SUMMARIES_MAX_RETRIES",
)
FAKE_KEY = "test-key-not-a-secret"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in _VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-empty"))
    monkeypatch.chdir(tmp_path)


def test_defaults_follow_the_spec() -> None:
    settings = SummariesSettings()

    assert settings.provider == "local"
    assert settings.local_base_url == "http://127.0.0.1:11434/v1"
    assert settings.local_model == "qwen3.5:9b"
    assert settings.local_context_tokens == 4096
    assert settings.unload_local_model is True
    assert settings.cloud_provider_name == "OpenCode Go"
    assert settings.cloud_base_url == "https://opencode.ai/zen/go/v1"
    assert settings.cloud_model == "deepseek-v4-pro"
    assert settings.cloud_context_tokens == 100000
    assert settings.language == "auto"
    assert settings.max_output_tokens == 2048
    assert settings.temperature == 0.2
    assert settings.timeout_seconds == 300
    assert settings.total_timeout_seconds == 3600
    assert settings.max_retries == 2


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUMMARIES_PROVIDER", "cloud")
    monkeypatch.setenv("SUMMARIES_CLOUD_MODEL", "glm-test")
    monkeypatch.setenv("SUMMARIES_LANGUAGE", "es")
    monkeypatch.setenv("SUMMARIES_MAX_RETRIES", "0")

    settings = SummariesSettings()

    assert (settings.provider, settings.cloud_model) == ("cloud", "glm-test")
    assert (settings.language, settings.max_retries) == ("es", 0)


def test_the_root_settings_compose_the_summaries_settings() -> None:
    assert Settings().summaries == SummariesSettings()


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434/v1",
        "http://localhost:11434/v1",
        "http://[::1]:11434/v1",
        "http://LOCALHOST/v1",
    ],
)
def test_a_loopback_local_url_is_accepted(url: str) -> None:
    assert SummariesSettings(local_base_url=url).local_base_url == url


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.5:11434/v1",
        "http://example.com/v1",
        "http://127.0.0.1.example.com/v1",
        "http://localhost.example.com/v1",
        "http://user@example.com:11434/v1",
        "not a url",
        "",
    ],
)
def test_a_non_loopback_local_url_fails_naming_the_variable(url: str) -> None:
    with pytest.raises(ValidationError, match="SUMMARIES_LOCAL_BASE_URL"):
        SummariesSettings(local_base_url=url)


@pytest.mark.parametrize(
    "url", ["http://opencode.ai/zen/go/v1", "ftp://example.com", "example.com"]
)
def test_a_cloud_url_without_https_fails_naming_the_variable(url: str) -> None:
    with pytest.raises(ValidationError, match="SUMMARIES_CLOUD_BASE_URL"):
        SummariesSettings(cloud_base_url=url)


def test_the_api_key_is_not_a_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUMMARIES_CLOUD_API_KEY", FAKE_KEY)

    settings = SummariesSettings()

    assert "cloud_api_key" not in SummariesSettings.model_fields
    assert not hasattr(settings, "cloud_api_key")
    for rendered in (repr(settings), str(settings), repr(Settings()), settings.model_dump_json()):
        assert FAKE_KEY not in rendered


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SUMMARIES_PROVIDER", "other"),
        ("SUMMARIES_LANGUAGE", "Spanish"),
        ("SUMMARIES_LANGUAGE", "../es"),
        ("SUMMARIES_MAX_OUTPUT_TOKENS", "255"),
        ("SUMMARIES_LOCAL_CONTEXT_TOKENS", "0"),
        ("SUMMARIES_TEMPERATURE", "1.5"),
        ("SUMMARIES_TEMPERATURE", "-0.1"),
        ("SUMMARIES_TIMEOUT_SECONDS", "0"),
        ("SUMMARIES_TOTAL_TIMEOUT_SECONDS", "0"),
        ("SUMMARIES_MAX_RETRIES", "-1"),
    ],
)
def test_invalid_values_fail_at_load(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        SummariesSettings()


def test_a_context_that_cannot_hold_the_output_and_the_margin_fails() -> None:
    with pytest.raises(ValidationError, match="SUMMARIES_LOCAL_CONTEXT_TOKENS"):
        SummariesSettings(local_context_tokens=2048, max_output_tokens=2048)


def test_labels_and_limits_come_from_the_settings() -> None:
    settings = SummariesSettings(cloud_model="glm-test")

    assert settings.local_label == "Local (qwen3.5:9b)"
    assert settings.cloud_label == "cloud: OpenCode Go, glm-test"
    assert "cloud" in settings.cloud_label
    local = settings.limits_for(cloud=False)
    assert (local.model, local.context_tokens) == ("qwen3.5:9b", 4096)
    cloud = settings.limits_for(cloud=True)
    assert (cloud.model, cloud.context_tokens) == ("glm-test", 100000)
    assert cloud.temperature == 0.2
    assert cloud.max_output_tokens == 2048


def test_the_loopback_error_names_the_variable_and_the_accepted_hosts() -> None:
    with pytest.raises(ValidationError) as error:
        SummariesSettings(local_base_url="http://example.com/v1")

    message = str(error.value)
    assert "SUMMARIES_LOCAL_BASE_URL" in message
    assert "http or https" in message
    assert "127.0.0.1" in message


def test_cloud_provider_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SummariesSettings()

    assert settings.cloud_provider == "opencode-go"
    assert settings.cloud_provider_name == "OpenCode Go"
    assert settings.cloud_base_url == "https://opencode.ai/zen/go/v1"
    with pytest.raises(ValidationError):
        SummariesSettings(cloud_provider="other")  # type: ignore[arg-type]
    monkeypatch.setenv("SUMMARIES_CLOUD_BASE_URL", "https://gateway.example.test/v1")
    overridden = SummariesSettings()
    assert overridden.cloud_base_url == "https://gateway.example.test/v1"
    assert overridden.cloud_provider_name == "OpenCode Go"


def test_the_cloud_provider_identity_has_one_source() -> None:
    fields = SummariesSettings.model_fields
    default = CLOUD_PROVIDERS[DEFAULT_CLOUD_PROVIDER]

    assert fields["cloud_provider"].default == DEFAULT_CLOUD_PROVIDER
    assert fields["cloud_provider_name"].default == default.name
    assert fields["cloud_base_url"].default == default.base_url
