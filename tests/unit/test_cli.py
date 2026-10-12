"""The console script: no arguments serves, ``prefetch-models`` downloads the models."""

import io

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


def _capture_prefetch(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    seen: list[dict[str, object]] = []

    def fake_prefetch(*_: object, **options: object) -> int:
        seen.append(options)
        return 0

    monkeypatch.setattr(cli, "prefetch_models", fake_prefetch)
    monkeypatch.setattr(cli, "get_settings", Settings)
    return seen


def test_prefetch_models_without_options_uses_no_token_and_prints_prose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _capture_prefetch(monkeypatch)

    cli.run(["prefetch-models"], stdin=io.StringIO("never read\n"))

    assert [(o["token"], o["progress_json"]) for o in seen] == [(None, False)]


def test_token_stdin_reads_one_line_and_never_takes_the_token_from_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _capture_prefetch(monkeypatch)
    stdin = io.StringIO("hf_FromStdin123\nsecond line\n")

    code = cli.run(["prefetch-models", "--token-stdin", "--progress-json"], stdin=stdin)

    assert code == 0
    assert [(o["token"], o["progress_json"]) for o in seen] == [("hf_FromStdin123", True)]
    with pytest.raises(SystemExit):  # there is no way to pass the token as an argument
        cli.run(["prefetch-models", "--token", "hf_x"])


def test_token_stdin_with_nothing_on_stdin_is_a_usage_error_that_names_no_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = _capture_prefetch(monkeypatch)

    code = cli.run(["prefetch-models", "--token-stdin"], stdin=io.StringIO("  \n"))

    assert code == 2
    assert seen == []
    assert "standard input" in capsys.readouterr().err


def test_check_runs_the_offline_probe_and_nothing_else(monkeypatch: pytest.MonkeyPatch) -> None:
    from meeting_scribe.speakers.infrastructure.pyannote_backend import (  # noqa: PLC0415
        RealPyannoteBackend,
    )
    from meeting_scribe.transcription.infrastructure.faster_whisper_backend import (  # noqa: PLC0415
        FasterWhisperBackend,
    )

    seen: list[tuple[object, ...]] = []

    def fake_check(*args: object, **_: object) -> int:
        seen.append(args)
        return 1

    def no_download(*_: object, **__: object) -> int:
        raise AssertionError("--check must not download")

    monkeypatch.setattr(cli, "check_models", fake_check)
    monkeypatch.setattr(cli, "prefetch_models", no_download)
    monkeypatch.setattr(cli, "get_settings", Settings)

    assert cli.run(["prefetch-models", "--check"]) == 1

    ((whisper, pipeline, probe_whisper, probe_pipeline),) = seen
    assert (whisper, pipeline) == (
        "large-v3-turbo",
        "pyannote/speaker-diarization-community-1",
    )
    assert getattr(probe_whisper, "__func__", None) is FasterWhisperBackend.is_model_cached
    assert getattr(probe_pipeline, "__func__", None) is RealPyannoteBackend.is_pipeline_cached


def test_check_cannot_be_combined_with_a_download_option() -> None:
    with pytest.raises(SystemExit) as stopped:
        cli.run(["prefetch-models", "--check", "--token-stdin"])

    assert stopped.value.code == 2


def test_the_module_can_be_run_with_python_dash_m() -> None:
    import subprocess  # noqa: PLC0415
    import sys  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    src = Path(__file__).resolve().parents[2] / "src"
    out = subprocess.run(
        [sys.executable, "-m", "meeting_scribe.cli", "prefetch-models", "--help"],
        capture_output=True,
        text=True,
        check=True,
        env={"PYTHONPATH": str(src)},
    ).stdout

    assert "--token-stdin" in out
    assert "--progress-json" in out
    assert "--check" in out


def test_a_download_passes_the_orphan_cleanup_of_both_backends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from meeting_scribe.speakers.infrastructure.pyannote_backend import (  # noqa: PLC0415
        RealPyannoteBackend,
    )
    from meeting_scribe.transcription.infrastructure.faster_whisper_backend import (  # noqa: PLC0415
        FasterWhisperBackend,
    )

    seen = _capture_prefetch(monkeypatch)

    cli.run(["prefetch-models"])

    (options,) = seen
    whisper, pipeline = options["discard_whisper"], options["discard_pipeline"]
    assert getattr(whisper, "__func__", None) is FasterWhisperBackend.discard_incomplete
    assert getattr(pipeline, "__func__", None) is RealPyannoteBackend.discard_incomplete
