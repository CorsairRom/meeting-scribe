import pytest
from returns.result import Failure

from meeting_scribe.base.domain.errors import (
    GpuOutOfMemoryError,
    GpuUnavailableError,
    ModelUnavailableError,
)
from meeting_scribe.speakers.domain.diarization import CUDA_REMEDY, check_device_available
from meeting_scribe.speakers.domain.errors import DiarizationError, DiarizationModelAccessError
from meeting_scribe.speakers.domain.turns import Turn
from meeting_scribe.speakers.infrastructure.pyannote_diarizer import (
    PyannoteDiarizer,
    map_pyannote_error,
    progress_hook,
)
from tests.unit.speakers.fakes import AUDIO, PARAMS, FakeBackend


class GatedRepoError(Exception):
    """Same name as huggingface_hub's, which is all the mapping looks at."""


class LocalTokenNotFoundError(OSError):
    pass


class OutOfMemoryError(RuntimeError):
    pass


class _Response:
    status_code = 403


class HfHubHTTPError(OSError):
    response = _Response()


def _diarize(backend: FakeBackend, device: str = "cuda", on_progress=lambda _: None):
    return PyannoteDiarizer(backend).diarize(AUDIO, PARAMS, device, on_progress)  # type: ignore[arg-type]


def test_both_annotations_become_sorted_turns() -> None:
    backend = FakeBackend()
    backend.turns = (
        Turn(start=5, end=6, speaker="SPEAKER_01"),
        Turn(start=0, end=4, speaker="SPEAKER_00"),
    )

    result = _diarize(backend).unwrap()

    assert [(t.start, t.speaker) for t in result.turns] == [(0, "SPEAKER_00"), (5, "SPEAKER_01")]
    assert [t.speaker for t in result.exclusive] == ["SPEAKER_00", "SPEAKER_01"]


def test_the_speaker_bounds_reach_the_pipeline_and_the_input_is_a_waveform_dict() -> None:
    backend = FakeBackend()
    params = PARAMS.model_copy(update={"num_speakers": 3, "min_speakers": 2, "max_speakers": 4})

    PyannoteDiarizer(backend).diarize(AUDIO, params, "cuda", lambda _: None)  # type: ignore[arg-type]

    file, num, low, high = backend.calls[0]
    assert (num, low, high) == (3, 2, 4)
    assert file == {"waveform": AUDIO, "sample_rate": 16_000}


def test_telemetry_is_opted_out_before_the_pipeline_is_loaded() -> None:
    backend = FakeBackend()

    _diarize(backend)

    assert backend.events == ["prepare", f"load:{PARAMS.pipeline}:cuda"]


def test_cuda_required_never_falls_back_to_the_cpu() -> None:
    backend = FakeBackend()
    backend.cuda_devices = 0

    result = _diarize(backend)

    assert isinstance(result, Failure)
    assert isinstance(result.failure(), GpuUnavailableError)
    assert "PROCESSING_DEVICE=cpu" in str(result.failure())
    assert not any(event.startswith("load") for event in backend.events)


def test_the_cpu_needs_no_cuda_device() -> None:
    backend = FakeBackend()
    backend.cuda_devices = 0

    assert _diarize(backend, device="cpu").unwrap().exclusive
    assert f"load:{PARAMS.pipeline}:cpu" in backend.events


@pytest.mark.parametrize(
    "error", [GatedRepoError("403 gated"), LocalTokenNotFoundError("no token"), HfHubHTTPError("x")]
)
def test_gated_model_error_maps_to_access_denied(error: Exception) -> None:
    backend = FakeBackend()
    backend.load_error = error

    failure = _diarize(backend).failure()

    assert isinstance(failure, DiarizationModelAccessError)
    assert failure.error_code == "DIARIZATION_MODEL_ACCESS_DENIED"
    assert f"https://hf.co/{PARAMS.pipeline}" in str(failure)
    assert "Preferences" in str(failure)
    assert "Download models" in str(failure)
    assert "hf auth login" not in str(failure)
    assert "403 gated" not in str(failure)  # the library's text is never repeated


def test_out_of_memory_maps_to_its_code_wherever_it_happens() -> None:
    backend = FakeBackend()
    backend.run_error = OutOfMemoryError("CUDA out of memory. Tried to allocate 2.00 GiB")

    failure = _diarize(backend).failure()

    assert isinstance(failure, GpuOutOfMemoryError)
    assert failure.error_code == "GPU_OUT_OF_MEMORY"


def test_other_failures_keep_their_class_and_message() -> None:
    assert isinstance(map_pyannote_error(ValueError("bad shape"), "p"), DiarizationError)
    assert "ValueError: bad shape" in str(map_pyannote_error(ValueError("bad shape"), "p"))
    assert "uv sync --extra ml" in str(map_pyannote_error(ImportError("torch"), "p"))
    assert isinstance(
        map_pyannote_error(RuntimeError("Found no NVIDIA driver on your system"), "p"),
        GpuUnavailableError,
    )


@pytest.mark.parametrize(
    "message",
    [
        "CUDA driver initialization failed, you might not have a CUDA gpu.",
        "CUDA driver version is insufficient for CUDA runtime version",
        "no CUDA-capable device is detected",
    ],
)
def test_real_cuda_absence_maps_to_gpu_unavailable(message: str) -> None:
    assert isinstance(map_pyannote_error(RuntimeError(message), "p"), GpuUnavailableError)


def test_a_mid_run_cuda_driver_failure_on_a_present_device_is_not_gpu_unavailable() -> None:
    error = RuntimeError("CUDA driver error: an illegal memory access was encountered")

    mapped = map_pyannote_error(error, "p")

    assert isinstance(mapped, DiarizationError)
    assert not isinstance(mapped, GpuUnavailableError)


def test_the_cuda_remedy_is_one_shared_text() -> None:
    absent = map_pyannote_error(RuntimeError("Found no NVIDIA driver"), "p")

    assert CUDA_REMEDY in str(absent)
    assert CUDA_REMEDY in str(check_device_available("cuda", 0).failure())


def test_an_offline_cache_miss_is_a_model_unavailable_error() -> None:
    class LocalEntryNotFoundError(FileNotFoundError):
        pass

    assert isinstance(
        map_pyannote_error(LocalEntryNotFoundError("offline"), "p"), ModelUnavailableError
    )


def test_pipeline_steps_map_to_a_monotonic_fraction_and_print_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    backend = FakeBackend()
    backend.steps = (
        ("segmentation", 10, 0),
        ("segmentation", 10, 10),
        ("speaker_counting", None, None),
        ("embeddings", 4, 2),
        ("embeddings", 4, 4),
        ("discrete_diarization", None, None),
    )
    seen: list[float] = []

    _diarize(backend, on_progress=seen.append)

    assert seen == pytest.approx([0.0, 0.4, 0.4 + 0.55 / 2, 0.95, 1.0])
    assert capsys.readouterr().out == ""


def test_the_hook_ignores_unknown_steps_and_zero_totals() -> None:
    seen: list[float] = []
    hook = progress_hook(seen.append)

    hook("something_new", None, total=0, completed=0)
    hook("segmentation", None, total=0, completed=0)

    assert seen == [0.0]


def test_an_offline_cache_miss_tells_the_user_to_download_the_models_in_preferences() -> None:
    class LocalEntryNotFoundError(FileNotFoundError):
        pass

    message = str(map_pyannote_error(LocalEntryNotFoundError("offline"), "p"))

    assert "Preferences" in message
    assert "Download models" in message
    assert "make install" not in message
