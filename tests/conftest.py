import logging
import shutil
from collections.abc import Iterator

import pytest


@pytest.fixture
def restore_root_logger() -> Iterator[None]:
    """Undo any root-logger changes (handlers, level) made by a test."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip ``ffmpeg`` / ``dbus`` marked tests when their binary is not installed."""
    for marker, binary in (("ffmpeg", "ffmpeg"), ("dbus", "dbus-daemon")):
        if shutil.which(binary) is not None:
            continue
        skip = pytest.mark.skip(reason=f"{binary} is not on PATH")
        for item in items:
            if marker in item.keywords:
                item.add_marker(skip)
