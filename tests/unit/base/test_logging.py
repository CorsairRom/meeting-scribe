import logging

import pytest

from meeting_scribe.base.infrastructure.logging import configure_logging


@pytest.mark.usefixtures("restore_root_logger")
def test_configure_logging_sets_root_level() -> None:
    configure_logging("DEBUG")
    assert logging.getLogger().level == logging.DEBUG

    configure_logging("warning")
    assert logging.getLogger().level == logging.WARNING
