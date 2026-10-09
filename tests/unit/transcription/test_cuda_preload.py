import logging
from pathlib import Path

import pytest

from meeting_scribe.transcription.infrastructure.cuda_preload import preload_cuda_libraries


class Loader:
    def __init__(self, broken: tuple[str, ...] = ()) -> None:
        self.loaded: list[str] = []
        self.broken = broken

    def __call__(self, path: str, mode: int = 0) -> None:
        if Path(path).name in self.broken:
            raise OSError(f"cannot open shared object file: {Path(path).name}")
        self.loaded.append(Path(path).name)


def _wheel(root: Path, package: str, *libraries: str) -> None:
    lib = root / package / "lib"
    lib.mkdir(parents=True)
    for name in libraries:
        (lib / name).write_bytes(b"")
    (lib / "README.txt").write_text("not a library", encoding="utf-8")


def test_every_shared_object_of_every_nvidia_wheel_is_loaded_in_a_stable_order(
    tmp_path: Path,
) -> None:
    _wheel(tmp_path, "cudnn", "libcudnn.so.9")
    _wheel(tmp_path, "cublas", "libcublasLt.so.12", "libcublas.so.12")
    loader = Loader()

    report = preload_cuda_libraries([tmp_path], loader)

    assert loader.loaded == ["libcublas.so.12", "libcublasLt.so.12", "libcudnn.so.9"]
    assert len(report.loaded) == 3
    assert report.failed == ()


def test_a_library_that_does_not_load_is_logged_by_name_and_does_not_stop_the_rest(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _wheel(tmp_path, "cublas", "libbad.so.12", "libgood.so.12")
    loader = Loader(broken=("libbad.so.12",))

    with caplog.at_level(logging.WARNING):
        report = preload_cuda_libraries([tmp_path], loader)

    assert loader.loaded == ["libgood.so.12"]
    assert [name for name, _ in report.failed] == ["libbad.so.12"]
    assert "libbad.so.12" in caplog.text
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_no_nvidia_wheels_is_reported_not_an_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        report = preload_cuda_libraries([tmp_path / "missing"], Loader())

    assert report.loaded == () and report.failed == ()
    assert "No NVIDIA CUDA libraries" in caplog.text
