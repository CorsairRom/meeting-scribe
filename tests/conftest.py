import logging
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
