"""Load the pip-installed NVIDIA libraries before CTranslate2 needs them (GO-001).

``nvidia-cublas-cu12`` and ``nvidia-cudnn-cu12`` put their ``.so`` files under
``site-packages/nvidia/*/lib``, which is on no loader path. This module imports nothing from the
ML stack, so importing it is free; call it before importing ``faster_whisper``.
"""

import ctypes
import importlib.util
import logging
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

type Loader = Callable[..., object]


class PreloadReport(NamedTuple):
    loaded: tuple[str, ...]
    failed: tuple[tuple[str, str], ...]  # (library file name, reason)


def nvidia_roots() -> list[Path]:
    """Directories of the ``nvidia`` namespace package (empty when no wheel is installed)."""
    spec = importlib.util.find_spec("nvidia")
    return [Path(location) for location in (spec.submodule_search_locations or [])] if spec else []


def preload_cuda_libraries(
    roots: Iterable[Path] | None = None, loader: Loader = ctypes.CDLL
) -> PreloadReport:
    """``loader(path, mode=RTLD_GLOBAL)`` for every ``<root>/*/lib/*.so*``; log every failure."""
    libraries = sorted(
        (
            path
            for root in (nvidia_roots() if roots is None else roots)
            for path in root.glob("*/lib/*.so*")
        ),
        key=lambda path: (path.parent.parent.name, path.name),
    )
    if not libraries:
        logger.info("No NVIDIA CUDA libraries found in the Python environment; nothing to preload.")
    loaded: list[str] = []
    failed: list[tuple[str, str]] = []
    for path in libraries:
        try:
            loader(str(path), mode=ctypes.RTLD_GLOBAL)
        except OSError as error:
            logger.warning("Cannot preload CUDA library %s: %s", path.name, error)
            failed.append((path.name, str(error)))
        else:
            loaded.append(path.name)
    if libraries:
        logger.info("Preloaded %d CUDA libraries (%d failed).", len(loaded), len(failed))
    return PreloadReport(tuple(loaded), tuple(failed))
