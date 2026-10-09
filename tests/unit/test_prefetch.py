"""``meeting-scribe prefetch-models``: downloads once, with fakes only (never the network)."""

import io
import sys
import types

import pytest

from meeting_scribe.prefetch import prefetch_models

WHISPER = "large-v3-turbo"
PIPELINE = "pyannote/speaker-diarization-community-1"


def _hub_error(name: str, message: str = "boom") -> Exception:
    return type(name, (Exception,), {})(message)


class Calls:
    def __init__(
        self, whisper_error: Exception | None = None, pipeline_error: Exception | None = None
    ) -> None:
        self.order: list[str] = []
        self.whisper_error = whisper_error
        self.pipeline_error = pipeline_error

    def whisper(self, model: str) -> None:
        self.order.append(f"whisper:{model}")
        if self.whisper_error:
            raise self.whisper_error

    def pipeline(self, pipeline: str) -> None:
        self.order.append(f"pipeline:{pipeline}")
        if self.pipeline_error:
            raise self.pipeline_error


def _run(calls: Calls) -> tuple[int, str]:
    out = io.StringIO()
    code = prefetch_models(WHISPER, PIPELINE, calls.whisper, calls.pipeline, out=out)
    return code, out.getvalue()


def test_both_models_are_fetched_by_their_configured_names_gated_one_first() -> None:
    calls = Calls()

    code, text = _run(calls)

    assert code == 0
    assert calls.order == [f"pipeline:{PIPELINE}", f"whisper:{WHISPER}"]  # fail fast on the login
    assert WHISPER in text
    assert PIPELINE in text
    assert "2 of 2" in text


def test_a_gated_pipeline_says_what_to_do_and_the_other_model_is_still_fetched() -> None:
    calls = Calls(pipeline_error=_hub_error("GatedRepoError", "403 hf_SECRET123 gated"))

    code, text = _run(calls)

    assert code == 1
    assert "DIARIZATION_MODEL_ACCESS_DENIED" in text
    assert "hf auth login" in text
    assert f"https://hf.co/{PIPELINE}" in text
    assert f"whisper:{WHISPER}" in calls.order
    assert "1 of 2" in text
    assert "hf_SECRET123" not in text


@pytest.mark.parametrize("name", ["LocalTokenNotFoundError", "RepositoryNotFoundError"])
def test_a_missing_login_or_unknown_repo_is_the_same_actionable_message(name: str) -> None:
    code, text = _run(Calls(pipeline_error=_hub_error(name)))

    assert code == 1
    assert "hf auth login" in text


def test_a_network_failure_on_whisper_is_a_model_unavailable_failure() -> None:
    code, text = _run(Calls(whisper_error=_hub_error("LocalEntryNotFoundError")))

    assert code == 1
    assert "MODEL_UNAVAILABLE" in text
    assert WHISPER in text


def test_an_unexpected_error_never_prints_a_token() -> None:
    code, text = _run(Calls(whisper_error=RuntimeError("bad header Bearer hf_abcDEF123456")))

    assert code == 1
    assert "hf_abcDEF123456" not in text


def test_a_missing_ml_stack_is_reported_not_raised() -> None:
    code, text = _run(Calls(whisper_error=ImportError("No module named 'faster_whisper'")))

    assert code == 1
    assert "ml" in text


# -- the real fetchers, with the libraries replaced (never imported, never downloading) --------


def _fake_module(monkeypatch: pytest.MonkeyPatch, name: str, **attrs: object) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)
    return module


def test_the_whisper_backend_downloads_through_faster_whisper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from meeting_scribe.transcription.infrastructure.faster_whisper_backend import (  # noqa: PLC0415
        FasterWhisperBackend,
    )

    seen: list[str] = []
    utils = _fake_module(monkeypatch, "faster_whisper.utils", download_model=seen.append)
    _fake_module(monkeypatch, "faster_whisper", utils=utils)

    FasterWhisperBackend().download_model("large-v3-turbo")

    assert seen == ["large-v3-turbo"]


def test_the_pyannote_backend_fetches_with_the_ambient_login_and_loads_nothing_on_a_gpu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from meeting_scribe.speakers.infrastructure.pyannote_backend import (  # noqa: PLC0415
        RealPyannoteBackend,
    )

    seen: list[tuple[str, object]] = []

    class Pipeline:
        @staticmethod
        def from_pretrained(name: str, token: object = None) -> object:
            seen.append((name, token))
            return object()

    audio = _fake_module(monkeypatch, "pyannote.audio", Pipeline=Pipeline)
    _fake_module(monkeypatch, "pyannote", audio=audio)

    RealPyannoteBackend().fetch_pipeline("pyannote/x")

    assert seen == [("pyannote/x", True)]  # token=True: the stored login, never read here


def test_the_pyannote_fetch_fails_when_the_library_returns_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from meeting_scribe.speakers.infrastructure.pyannote_backend import (  # noqa: PLC0415
        RealPyannoteBackend,
    )

    class Pipeline:
        @staticmethod
        def from_pretrained(*_: object, **__: object) -> None:
            return None

    audio = _fake_module(monkeypatch, "pyannote.audio", Pipeline=Pipeline)
    _fake_module(monkeypatch, "pyannote", audio=audio)

    with pytest.raises(RuntimeError):
        RealPyannoteBackend().fetch_pipeline("pyannote/x")
