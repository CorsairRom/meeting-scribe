"""The client name sent to the cloud gateway: ``meeting-scribe/<package version>``."""

import importlib.metadata
import re

from meeting_scribe.summaries.infrastructure.client_identity import user_agent


def test_the_user_agent_is_the_package_name_and_version() -> None:
    assert user_agent() == f"meeting-scribe/{importlib.metadata.version('meeting-scribe')}"


def test_the_user_agent_carries_nothing_else() -> None:
    assert re.fullmatch(r"meeting-scribe/[0-9][^\s/]*", user_agent())
