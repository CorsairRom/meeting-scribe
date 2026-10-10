import pytest
from returns.result import Success

from meeting_scribe.base.domain.errors import GpuUnavailableError
from meeting_scribe.transcription.domain.params import TranscriptionParams, check_device_available
from tests.unit.transcription.fakes import PARAMS


def test_compute_type_follows_the_device() -> None:
    assert PARAMS.effective_compute_type("cuda") == "float16"
    assert PARAMS.effective_compute_type("cpu") == "int8"
    assert PARAMS.device_label("cuda") == "cuda/float16"
    assert PARAMS.device_label("cpu") == "cpu/int8"


@pytest.mark.parametrize(("language", "expected"), [("es", "es"), ("auto", None), ("AUTO", None)])
def test_auto_language_means_detection(language: str, expected: str | None) -> None:
    params = TranscriptionParams.model_validate({**PARAMS.model_dump(), "language": language})

    assert params.whisper_language() == expected


def test_params_round_trip_through_the_job_spec() -> None:
    spec = PARAMS.model_dump(mode="json")

    assert type(PARAMS).model_validate(spec) == PARAMS


def test_cuda_required_never_falls_back() -> None:
    result = check_device_available("cuda", cuda_device_count=0)

    error = result.failure()
    assert isinstance(error, GpuUnavailableError)
    assert "PROCESSING_DEVICE=cpu" in str(error)


def test_a_cuda_device_or_an_explicit_cpu_is_accepted() -> None:
    assert check_device_available("cuda", cuda_device_count=1) == Success("cuda")
    assert check_device_available("cpu", cuda_device_count=0) == Success("cpu")
