"""Choosing Cloud is the opt-in and there is no separate switch (AC-4)."""

import json
from pathlib import Path

from meeting_scribe.preferences.domain.fields import FIELD_REGISTRY
from meeting_scribe.preferences.infrastructure.json_settings_store import JsonSettingsStore
from meeting_scribe.summaries.infrastructure.summaries_settings import SummariesSettings
from tests.unit.meetings.test_summary_controller import Rig


def test_no_cloud_enabled_field_exists_anywhere() -> None:
    assert "cloud_enabled" not in SummariesSettings.model_fields
    assert not [s.key for s in FIELD_REGISTRY if "enabled" in s.key]


def test_the_settings_file_never_holds_a_cloud_switch(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    JsonSettingsStore(path).write({"summaries.provider": "cloud"}).unwrap()

    assert "enabled" not in json.dumps(json.loads(path.read_text(encoding="utf-8")))


def test_the_cloud_row_follows_the_provider() -> None:
    local_rig = Rig(default_provider="local")
    cloud_rig = Rig(default_provider="cloud")

    assert local_rig.config.cloud_enabled is False
    assert cloud_rig.config.cloud_enabled is True
    _, disabled = local_rig.controller.providers()
    _, enabled = cloud_rig.controller.providers()
    assert (disabled.available, disabled.reason) == (False, "SUMMARY_CLOUD_DISABLED")
    assert (enabled.available, enabled.default) == (True, True)
