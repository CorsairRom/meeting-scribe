"""The exposed-field registry (spec section 2) and the secret allow-list (AC-9)."""

import pytest

from meeting_scribe.preferences.domain.fields import (
    FIELD_REGISTRY,
    FieldCode,
    FieldProblem,
    FieldSource,
    field_spec,
)

EXPECTED = {
    "summaries.provider": ("choice", "live", ("local", "cloud")),
    "summaries.local_model": ("text", "live", ()),
    "summaries.cloud_provider": ("choice", "live", ("opencode-go",)),
    "summaries.language": ("choice", "live", ("auto",)),
    "summaries.cloud_model": ("text", "live", ()),
    "storage.meetings_dir": ("path", "restart", ()),
}


def test_the_registry_exposes_exactly_the_spec_fields() -> None:
    assert {s.key: (s.kind, s.apply, s.choices) for s in FIELD_REGISTRY} == EXPECTED


def test_only_the_meetings_folder_needs_a_restart() -> None:
    assert [s.key for s in FIELD_REGISTRY if s.apply == "restart"] == ["storage.meetings_dir"]


@pytest.mark.parametrize("key", [s.key for s in FIELD_REGISTRY])
def test_the_key_splits_into_section_and_name(key: str) -> None:
    spec = field_spec(key)
    assert spec is not None
    assert f"{spec.section}.{spec.name}" == key


def test_an_unknown_key_has_no_spec() -> None:
    assert field_spec("summaries.cloud_base_url") is None


def test_a_value_is_locked_only_by_the_environment_layers() -> None:
    assert [FieldSource(source).locked for source in ("default", "file", "env", "dotenv")] == [
        False,
        False,
        True,
        True,
    ]


def test_field_codes_are_the_spec_codes() -> None:
    assert {c.value for c in FieldCode} == {
        "FIELD_UNKNOWN",
        "FIELD_LOCKED",
        "FIELD_INVALID",
        "FIELD_FOLDER_NOT_WRITABLE",
    }
    assert FieldProblem("summaries.provider", FieldCode.INVALID, "bad").code == "FIELD_INVALID"
