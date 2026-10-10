"""The console script: no arguments serves, ``prefetch-models`` downloads the models."""

import pytest

from meeting_scribe import cli
from meeting_scribe.settings import Settings


def test_without_arguments_it_serves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.service, "run", lambda: 7)

    assert cli.run([]) == 7


def test_prefetch_models_uses_the_configured_names(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str]] = []

    def fake_prefetch(whisper: str, pipeline: str, *_: object, **__: object) -> int:
        seen.append((whisper, pipeline))
        return 0

    monkeypatch.setattr(cli, "prefetch_models", fake_prefetch)
    monkeypatch.setattr(cli, "get_settings", Settings)

    assert cli.run(["prefetch-models"]) == 0
    assert seen == [("large-v3-turbo", "pyannote/speaker-diarization-community-1")]


def test_prefetch_models_passes_the_real_fetchers_in_the_right_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from meeting_scribe.speakers.infrastructure.pyannote_backend import (  # noqa: PLC0415
        RealPyannoteBackend,
    )
    from meeting_scribe.transcription.infrastructure.faster_whisper_backend import (  # noqa: PLC0415
        FasterWhisperBackend,
    )

    seen: list[tuple[object, object]] = []

    def fake_prefetch(
        _whisper: str, _pipeline: str, fetch_whisper: object, fetch_pipeline: object, **__: object
    ) -> int:
        seen.append((fetch_whisper, fetch_pipeline))
        return 0

    monkeypatch.setattr(cli, "prefetch_models", fake_prefetch)
    monkeypatch.setattr(cli, "get_settings", Settings)

    cli.run(["prefetch-models"])

    ((fetch_whisper, fetch_pipeline),) = seen
    assert getattr(fetch_whisper, "__func__", None) is FasterWhisperBackend.download_model
    assert getattr(fetch_pipeline, "__func__", None) is RealPyannoteBackend.fetch_pipeline


def test_an_unknown_command_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as stopped:
        cli.run(["frobnicate"])

    assert stopped.value.code == 2
