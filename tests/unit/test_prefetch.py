"""``meeting-scribe prefetch-models``: downloads once, with fakes only (never the network)."""

import io
import json
import sys
import types

import pytest

from meeting_scribe.prefetch import check_models, prefetch_models

WHISPER = "large-v3-turbo"
PIPELINE = "pyannote/speaker-diarization-community-1"


def _hub_error(name: str, message: str = "boom") -> Exception:
    return type(name, (Exception,), {})(message)


class Calls:
    def __init__(
        self, whisper_error: Exception | None = None, pipeline_error: Exception | None = None
    ) -> None:
        self.order: list[str] = []
        self.tokens: list[str | None] = []
        self.whisper_error = whisper_error
        self.pipeline_error = pipeline_error

    def whisper(self, model: str) -> None:
        self.order.append(f"whisper:{model}")
        if self.whisper_error:
            raise self.whisper_error

    def pipeline(self, pipeline: str, token: str | None = None) -> None:
        self.order.append(f"pipeline:{pipeline}")
        self.tokens.append(token)
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
    assert "Preferences" in text
    assert "Download models" in text
    assert f"https://hf.co/{PIPELINE}" in text
    assert f"whisper:{WHISPER}" in calls.order
    assert "1 of 2" in text
    assert "hf_SECRET123" not in text


@pytest.mark.parametrize("name", ["LocalTokenNotFoundError", "RepositoryNotFoundError"])
def test_a_missing_login_or_unknown_repo_is_the_same_actionable_message(name: str) -> None:
    code, text = _run(Calls(pipeline_error=_hub_error(name)))

    assert code == 1
    assert "Preferences" in text


def test_a_network_failure_on_whisper_is_a_model_unavailable_failure() -> None:
    code, text = _run(Calls(whisper_error=_hub_error("LocalEntryNotFoundError")))

    assert code == 1
    assert "MODEL_UNAVAILABLE" in text
    assert WHISPER in text


def test_the_install_time_failure_text_is_not_the_workers_offline_text() -> None:
    code, text = _run(Calls(whisper_error=_hub_error("LocalEntryNotFoundError")))

    assert code == 1
    assert "worker runs offline" not in text
    assert "make install" not in text
    assert "network" in text
    assert "TRANSCRIPTION_MODEL" in text  # the name may be wrong
    assert "meeting-scribe prefetch-models" in text  # how to retry
    assert "LocalEntryNotFoundError" in text  # what failed


def test_the_pipeline_failure_names_its_setting_and_the_worker_keeps_its_own_text() -> None:
    from meeting_scribe.speakers.infrastructure.pyannote_diarizer import (  # noqa: PLC0415
        map_pyannote_error,
    )
    from meeting_scribe.transcription.infrastructure.faster_whisper_transcriber import (  # noqa: PLC0415
        map_whisper_error,
    )

    _, text = _run(Calls(pipeline_error=_hub_error("LocalEntryNotFoundError")))
    assert "SPEAKERS_PIPELINE" in text
    assert "worker runs offline" not in text

    error = _hub_error("LocalEntryNotFoundError")
    assert "worker runs offline" in str(map_whisper_error(error, WHISPER))  # worker path unchanged
    assert "worker runs offline" in str(map_pyannote_error(error, PIPELINE))


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


# -- F6.4: the token on stdin, the progress lines and the offline check --------------------------

SENTINEL = "hf_SENTINELtoken0123456789"


def _json_lines(text: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in text.splitlines()]


def test_the_token_is_passed_to_the_pipeline_fetcher_only() -> None:
    calls = Calls()
    out = io.StringIO()

    code = prefetch_models(
        WHISPER, PIPELINE, calls.whisper, calls.pipeline, out=out, token=SENTINEL
    )

    assert code == 0
    assert calls.tokens == [SENTINEL]
    assert SENTINEL not in out.getvalue()


def test_without_a_token_the_pipeline_fetcher_gets_none() -> None:
    calls = Calls()

    _run(calls)

    assert calls.tokens == [None]


def test_a_token_that_is_not_shaped_like_one_is_still_scrubbed_from_a_failure() -> None:
    plain = "plain-secret-without-prefix"
    calls = Calls(pipeline_error=RuntimeError(f"401 for {plain}"))
    out = io.StringIO()

    code = prefetch_models(WHISPER, PIPELINE, calls.whisper, calls.pipeline, out=out, token=plain)

    assert code == 1
    assert plain not in out.getvalue()


def test_progress_json_prints_one_object_per_line_pipeline_first_then_whisper() -> None:
    calls = Calls()
    out = io.StringIO()

    code = prefetch_models(
        WHISPER, PIPELINE, calls.whisper, calls.pipeline, out=out, progress_json=True
    )

    assert code == 0
    assert _json_lines(out.getvalue()) == [
        {"event": "step", "model": "pipeline", "step": 1, "total_steps": 2},
        {"event": "step", "model": "whisper", "step": 2, "total_steps": 2},
        {"event": "finished", "ready": True},
    ]


def test_progress_json_reports_each_failure_with_its_code_and_never_a_token() -> None:
    calls = Calls(pipeline_error=_hub_error("GatedRepoError", f"403 {SENTINEL} gated"))
    out = io.StringIO()

    code = prefetch_models(
        WHISPER,
        PIPELINE,
        calls.whisper,
        calls.pipeline,
        out=out,
        token=SENTINEL,
        progress_json=True,
    )

    lines = _json_lines(out.getvalue())
    assert code == 1
    errors = [line for line in lines if line["event"] == "error"]
    assert [(e["model"], e["error_code"]) for e in errors] == [
        ("pipeline", "DIARIZATION_MODEL_ACCESS_DENIED")
    ]
    assert lines[-1] == {"event": "finished", "ready": False}
    assert f"whisper:{WHISPER}" in calls.order  # the other model is still attempted
    assert SENTINEL not in out.getvalue()


def test_check_prints_one_json_line_and_exits_zero_when_both_are_cached() -> None:
    out = io.StringIO()
    asked: list[str] = []

    def cached(name: str) -> bool:
        asked.append(name)
        return True

    code = check_models(WHISPER, PIPELINE, cached, cached, out=out)

    assert code == 0
    assert json.loads(out.getvalue()) == {"whisper": True, "pipeline": True}
    assert out.getvalue().count("\n") == 1
    assert sorted(asked) == sorted([WHISPER, PIPELINE])


def test_check_exits_one_when_a_model_is_missing() -> None:
    out = io.StringIO()

    code = check_models(WHISPER, PIPELINE, lambda _: True, lambda _: False, out=out)

    assert code == 1
    assert json.loads(out.getvalue()) == {"whisper": True, "pipeline": False}


def test_check_treats_a_crashing_probe_as_not_cached() -> None:
    def boom(_: str) -> bool:
        raise RuntimeError("no hub")

    out = io.StringIO()

    assert check_models(WHISPER, PIPELINE, boom, boom, out=out) == 1
    assert json.loads(out.getvalue()) == {"whisper": False, "pipeline": False}


def test_the_whisper_backend_cache_check_is_local_only_and_needs_every_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from meeting_scribe.transcription.infrastructure.faster_whisper_backend import (  # noqa: PLC0415
        FasterWhisperBackend,
    )

    repo = tmp_path / "models--x--y"
    snapshot = repo / "snapshots" / "rev"
    (repo / "blobs").mkdir(parents=True)
    names = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json"]
    for index, name in enumerate([*names, "vocabulary.json"]):
        blob = repo / "blobs" / f"b{index}"
        blob.write_bytes(b"x")
        (snapshot / name).parent.mkdir(parents=True, exist_ok=True)
        (snapshot / name).symlink_to(blob)
    seen: list[tuple[str, bool]] = []

    def download_model(model: str, local_files_only: bool = False) -> str:
        seen.append((model, local_files_only))
        return str(snapshot)

    utils = _fake_module(monkeypatch, "faster_whisper.utils", download_model=download_model)
    _fake_module(monkeypatch, "faster_whisper", utils=utils)

    assert FasterWhisperBackend().is_model_cached("large-v3-turbo") is True
    assert seen == [("large-v3-turbo", True)]

    (snapshot / "model.bin").unlink()  # a missing weight file is not cached

    assert FasterWhisperBackend().is_model_cached("large-v3-turbo") is False


def test_the_whisper_cache_check_is_false_for_an_uncached_or_unknown_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from meeting_scribe.transcription.infrastructure.faster_whisper_backend import (  # noqa: PLC0415
        FasterWhisperBackend,
    )

    def download_model(*_: object, **__: object) -> str:
        raise _hub_error("LocalEntryNotFoundError")

    utils = _fake_module(monkeypatch, "faster_whisper.utils", download_model=download_model)
    _fake_module(monkeypatch, "faster_whisper", utils=utils)

    assert FasterWhisperBackend().is_model_cached("large-v3-turbo") is False
