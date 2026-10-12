"""The app duplicates the D-Bus names on purpose (it cannot import the service): no drift."""

from meeting_scribe.base.infrastructure.dbus import constants as service
from meeting_scribe.meetings.infrastructure.dbus import meetings_interface, summaries_interface
from meeting_scribe.model_setup.domain.models import ModelsStatus
from meeting_scribe.model_setup.infrastructure import keyring_token_source
from meeting_scribe.model_setup.infrastructure.dbus import models_interface
from meeting_scribe.preferences.domain.fields import FieldRow
from meeting_scribe.preferences.infrastructure.dbus import settings_interface
from meeting_scribe.summaries.infrastructure import secret_service_store
from meeting_scribe_review.core import constants as app

_ROW_MEMBERS = (
    "key", "kind", "value", "default", "source", "locked", "lock_reason", "apply", "choices",
    "file_problem", "apply_problem",
)  # fmt: skip


_MODELS_STATUS_MEMBERS = (
    "state", "whisper_cached", "pipeline_cached", "token_stored", "step", "total_steps",
    "failure_code",
)  # fmt: skip


def test_bus_name_object_path_and_error_prefix_match_the_service() -> None:
    assert app.BUS_NAME == service.BUS_NAME
    assert app.OBJECT_PATH == service.OBJECT_PATH
    assert app.ERROR_PREFIX == service.ERROR_PREFIX


def test_interface_name_matches_the_service() -> None:
    assert app.MEETINGS_INTERFACE == meetings_interface.INTERFACE_NAME


def test_every_member_the_app_calls_exists_on_the_service_interface() -> None:
    members = {name for name in dir(meetings_interface.MeetingsInterface) if name[0].isupper()}
    assert set(app.METHODS) <= members
    assert set(app.SIGNALS) <= members


def test_summaries_interface_name_matches_the_service() -> None:
    assert app.SUMMARIES_INTERFACE == summaries_interface.INTERFACE_NAME


def test_every_summaries_member_the_app_uses_exists_on_the_service_interface() -> None:
    members = {name for name in dir(summaries_interface.SummariesInterface) if name[0].isupper()}
    assert set(app.SUMMARY_METHODS) <= members
    assert set(app.SUMMARY_SIGNALS) <= members


def test_the_keyring_item_is_the_one_the_service_reads() -> None:
    assert app.KEY_ATTRIBUTES == secret_service_store.ATTRIBUTES
    assert app.KEY_LABEL == secret_service_store.LABEL


def test_settings_interface_name_matches_the_service() -> None:
    assert app.SETTINGS_INTERFACE == settings_interface.INTERFACE_NAME


def test_every_settings_member_the_app_uses_exists_on_the_service_interface() -> None:
    members = {name for name in dir(settings_interface.SettingsInterface) if name[0].isupper()}
    assert set(app.SETTINGS_METHODS) <= members
    assert set(app.SETTINGS_SIGNALS) <= members
    assert set(app.SETTINGS_PROPERTIES) <= members


def test_the_getall_row_has_every_member_the_app_parser_reads() -> None:
    row = settings_interface.field_row(
        FieldRow("k", "text", "v", "d", "default", False, "", "live", (), "", "")
    )
    assert set(row) == set(_ROW_MEMBERS)


def test_the_hugging_face_token_item_is_the_one_the_service_reads() -> None:
    assert app.HF_ATTRIBUTES == keyring_token_source.ATTRIBUTES
    assert app.HF_LABEL == keyring_token_source.LABEL
    assert app.HF_ATTRIBUTES != app.KEY_ATTRIBUTES  # a different item, never the cloud key


def test_models_interface_name_matches_the_service() -> None:
    assert app.MODELS_INTERFACE == models_interface.INTERFACE_NAME


def test_every_models_member_the_app_uses_exists_on_the_service_interface() -> None:
    members = {name for name in dir(models_interface.ModelsInterface) if name[0].isupper()}
    assert set(app.MODELS_METHODS) <= members
    assert set(app.MODELS_SIGNALS) <= members


def test_the_getstatus_row_has_every_member_the_app_parser_reads() -> None:
    row = models_interface.status_details(ModelsStatus("PENDING", False, False, False))
    assert set(row) == set(_MODELS_STATUS_MEMBERS)
