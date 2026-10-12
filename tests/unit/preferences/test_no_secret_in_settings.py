"""The API key never crosses D-Bus and is never a setting (AC-9, BR-005)."""

import json
import logging
from pathlib import Path

import pytest

from meeting_scribe.preferences.domain.fields import FIELD_REGISTRY
from meeting_scribe.preferences.infrastructure.json_settings_store import JsonSettingsStore
from tests.unit.preferences.fakes import Service

FAKE_KEY = "fake-api-key-not-a-secret"


def test_the_exposed_keys_are_an_allow_list_without_anything_secret_like() -> None:
    """Adding a field means reviewing this list: it must never carry a key, token or URL."""
    assert [s.key for s in FIELD_REGISTRY] == [
        "summaries.provider",
        "summaries.local_model",
        "summaries.language",
        "summaries.cloud_model",
        "storage.meetings_dir",
    ]
    forbidden = ("key", "token", "secret", "password", "url", "credential")
    assert not [s.key for s in FIELD_REGISTRY if any(w in s.key for w in forbidden)]


def test_a_key_submitted_as_a_setting_is_rejected_and_never_stored_or_logged(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    fx = Service(tmp_path)
    path = tmp_path / "settings.json"
    with caplog.at_level(logging.DEBUG):
        outcome = fx.service.set(
            {"summaries.api_key": FAKE_KEY, "summaries.cloud_api_key": FAKE_KEY}
        ).unwrap()
        fx.service.set({"summaries.cloud_model": "ok"}).unwrap()
    JsonSettingsStore(path).write(fx.store.values).unwrap()

    assert {e.code for e in outcome.errors} == {"FIELD_UNKNOWN"}
    assert FAKE_KEY not in caplog.text
    assert FAKE_KEY not in " ".join(e.message for e in outcome.errors)
    assert FAKE_KEY not in path.read_text(encoding="utf-8")
    assert set(json.loads(path.read_text(encoding="utf-8"))["values"]) == {"summaries.cloud_model"}
    assert FAKE_KEY not in repr(fx.service.get_all())
