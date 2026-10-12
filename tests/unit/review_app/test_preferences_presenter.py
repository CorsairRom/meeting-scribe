"""The Preferences presenter: staged edits, groups that follow the provider, one atomic Save.

Fake gateway and fake key store only: no bus, no keyring, no window (AC-5, AC-15).
"""

from dataclasses import replace

from meeting_scribe_review.core.errors import SERVICE_NOT_RUNNING, ServiceError
from meeting_scribe_review.core.models import FieldIssue, KeyStatus, SetOutcome
from meeting_scribe_review.core.presenters.preferences import (
    PreferencesPhase,
    PreferencesPresenter,
    PreferencesState,
)
from meeting_scribe_review.core.result import Err, Ok
from tests.unit.review_app.fakes import FakeApiKeyStore, FakeSettingsGateway, setting

PROVIDER = "summaries.provider"
LOCAL = "summaries.local_model"
CLOUD = "summaries.cloud_model"
LANGUAGE = "summaries.language"
FOLDER = "storage.meetings_dir"


def _wire(
    gateway: FakeSettingsGateway | None = None, keys: FakeApiKeyStore | None = None
) -> tuple[PreferencesPresenter, FakeSettingsGateway, FakeApiKeyStore, list[PreferencesState]]:
    gateway = gateway or FakeSettingsGateway()
    keys = keys or FakeApiKeyStore()
    states: list[PreferencesState] = []
    presenter = PreferencesPresenter(gateway, keys, states.append)
    return presenter, gateway, keys, states


def _loaded() -> tuple[PreferencesPresenter, FakeSettingsGateway, FakeApiKeyStore]:
    presenter, gateway, keys, _ = _wire()
    presenter.load()
    return presenter, gateway, keys


def _visible(state: PreferencesState) -> list[str]:
    return [f.key for f in state.fields if f.visible]


def test_loading_shows_the_fields_with_their_saved_values() -> None:
    presenter, _, _ = _loaded()
    state = presenter.state
    assert state.phase is PreferencesPhase.READY
    assert state.field(PROVIDER).value == "local"
    assert state.field(FOLDER).value == "/fake/meetings"
    assert not state.can_save


def test_the_first_state_is_loading() -> None:
    presenter, _, _, states = _wire()
    presenter.load()
    assert states[0].phase is PreferencesPhase.LOADING


def test_groups_follow_the_provider() -> None:
    presenter, _, _ = _loaded()
    assert LOCAL in _visible(presenter.state)
    assert CLOUD not in _visible(presenter.state)
    assert not presenter.state.show_cloud

    presenter.edit(PROVIDER, "cloud")

    assert CLOUD in _visible(presenter.state)
    assert LOCAL not in _visible(presenter.state)
    assert presenter.state.show_cloud
    assert "only when you press Generate summary" in presenter.state.cloud_note
    assert "asked every time" in presenter.state.cloud_note


def test_an_edit_is_staged_and_nothing_is_saved_until_save() -> None:
    presenter, gateway, _ = _loaded()
    presenter.edit(PROVIDER, "cloud")
    presenter.edit(LANGUAGE, "es")
    assert gateway.args_of("set_values") == []
    assert presenter.state.can_save
    assert presenter.state.field(LANGUAGE).edited


def test_editing_back_to_the_saved_value_is_not_an_edit() -> None:
    presenter, _, _ = _loaded()
    presenter.edit(LANGUAGE, "es")
    presenter.edit(LANGUAGE, "auto")
    assert not presenter.state.can_save
    assert not presenter.state.field(LANGUAGE).edited


def test_save_sends_only_the_changed_fields_in_one_call() -> None:
    presenter, gateway, _ = _loaded()
    presenter.edit(PROVIDER, "cloud")
    presenter.edit(CLOUD, "fake-model-x")

    presenter.save()

    assert gateway.args_of("set_values") == [({PROVIDER: "cloud", CLOUD: "fake-model-x"},)]
    assert presenter.state.saved
    assert presenter.state.field(PROVIDER).value == "cloud"
    assert not presenter.state.can_save


def test_save_with_nothing_changed_calls_nothing() -> None:
    presenter, gateway, _ = _loaded()
    presenter.save()
    assert gateway.args_of("set_values") == []


def test_field_problems_stay_on_their_rows_and_keep_the_edits() -> None:
    presenter, gateway, _ = _loaded()
    gateway.errors = (
        FieldIssue(LANGUAGE, "FIELD_INVALID", "Use auto or a lowercase language code."),
        FieldIssue(FOLDER, "FIELD_FOLDER_NOT_WRITABLE", "The meetings folder is not writable."),
    )
    presenter.edit(LANGUAGE, "Not Valid")
    presenter.edit(FOLDER, "/fake/other")

    presenter.save()

    state = presenter.state
    assert not state.saved
    assert state.field(LANGUAGE).error == "Use auto or a lowercase language code."
    assert state.field(FOLDER).error == "The meetings folder is not writable."
    assert state.field(LANGUAGE).value == "Not Valid"
    assert state.can_save
    presenter.edit(LANGUAGE, "es")
    assert presenter.state.field(LANGUAGE).error == ""
    assert presenter.state.field(FOLDER).error != ""


def test_a_locked_field_cannot_be_edited_and_says_where_it_comes_from() -> None:
    gateway = FakeSettingsGateway()
    gateway.fields[1] = setting(LOCAL, "env-model", source="env", locked=True)
    gateway.fields[2] = setting(
        LANGUAGE, "es", kind="choice", choices=("auto",), source="dotenv", locked=True
    )
    presenter, _, _, _ = _wire(gateway)
    presenter.load()

    presenter.edit(LOCAL, "other")

    state = presenter.state
    assert state.field(LOCAL).value == "env-model"
    assert state.field(LOCAL).locked
    assert "environment" in state.field(LOCAL).lock_text
    assert ".env" in state.field(LANGUAGE).lock_text
    assert not state.can_save


def test_the_legacy_switch_explains_how_to_unlock_the_provider() -> None:
    gateway = FakeSettingsGateway()
    gateway.fields[0] = setting(
        PROVIDER,
        "local",
        kind="choice",
        choices=("local", "cloud"),
        source="env",
        locked=True,
        lock_reason="LEGACY_SETTING",
    )
    presenter, _, _, _ = _wire(gateway)
    presenter.load()
    text = presenter.state.field(PROVIDER).lock_text
    assert "SUMMARIES_CLOUD_ENABLED" in text
    assert "delete" in text.lower()


def test_the_language_options_are_curated_and_keep_an_unlisted_saved_code() -> None:
    gateway = FakeSettingsGateway()
    gateway.fields[2] = setting(LANGUAGE, "sv", kind="choice", choices=("auto",))
    presenter, _, _, _ = _wire(gateway)
    presenter.load()
    options = dict(presenter.state.field(LANGUAGE).options)
    assert options["auto"] == "Meeting language"
    assert options["es"] == "Spanish"
    assert "sv" in options


def test_a_restart_field_shows_the_banner_and_save_stays_open() -> None:
    presenter, gateway, _ = _loaded()
    gateway.pending = (FOLDER,)
    presenter.edit(FOLDER, "/fake/new")

    presenter.save()

    assert presenter.state.restart_text == "Restart the service to use the new folder."
    assert presenter.state.can_restart
    assert not presenter.state.saved  # the banner must stay visible


def test_a_live_save_closes_even_while_a_restart_is_pending() -> None:
    presenter, gateway, _ = _loaded()
    gateway.pending = (FOLDER,)
    presenter.edit(LANGUAGE, "es")

    presenter.save()

    assert presenter.state.saved
    assert presenter.state.restart_text != ""


def test_a_pending_restart_is_read_when_loading() -> None:
    gateway = FakeSettingsGateway(pending=(FOLDER,))
    presenter, _, _, _ = _wire(gateway)
    presenter.load()
    assert presenter.state.can_restart


def test_restart_asks_the_service_and_reports_busy() -> None:
    presenter, gateway, _ = _loaded()
    gateway.pending = (FOLDER,)
    presenter.load()
    gateway.replies["restart"] = Err(ServiceError("SETTINGS_RESTART_BUSY"))

    presenter.restart()

    assert not presenter.state.restarting
    assert "Restart when it ends" in presenter.state.message

    gateway.replies["restart"] = Ok("RESTARTING")
    presenter.restart()
    assert presenter.state.restarting
    assert not presenter.state.can_restart


def test_restart_does_nothing_when_nothing_is_pending() -> None:
    presenter, gateway, _ = _loaded()
    presenter.restart()
    assert gateway.args_of("restart") == []


def test_after_the_service_comes_back_the_window_reloads() -> None:
    presenter, gateway, _ = _loaded()
    gateway.pending = (FOLDER,)
    presenter.load()
    presenter.restart()
    gateway.pending = ()
    presenter.on_service_available(False)
    assert presenter.state.restarting  # expected: the service is going away

    presenter.on_service_available(True)

    assert not presenter.state.restarting
    assert not presenter.state.can_restart
    assert presenter.state.phase is PreferencesPhase.READY


def test_a_service_that_is_not_running_makes_a_read_only_dialog_with_the_command() -> None:
    gateway = FakeSettingsGateway(replies={"get_all": Err(ServiceError(SERVICE_NOT_RUNNING))})
    presenter, _, _, _ = _wire(gateway)
    presenter.load()
    state = presenter.state
    assert state.phase is PreferencesPhase.UNAVAILABLE
    assert state.message == "The Meeting Scribe service is not running"
    assert state.hint == "systemctl --user start meeting-scribe.service"
    presenter.edit(LANGUAGE, "es")
    assert not presenter.state.can_save


def test_a_service_that_stops_while_open_blocks_save_but_keeps_the_edits() -> None:
    presenter, _, _ = _loaded()
    presenter.edit(LANGUAGE, "es")
    presenter.on_service_available(False)
    assert presenter.state.phase is PreferencesPhase.UNAVAILABLE
    assert not presenter.state.can_save
    presenter.on_service_available(True)
    assert presenter.state.field(LANGUAGE).value == "es"
    assert presenter.state.can_save


def test_a_change_made_elsewhere_updates_unedited_fields_only() -> None:
    presenter, gateway, _ = _loaded()
    presenter.edit(LANGUAGE, "es")
    gateway.fields = [
        replace(
            f, value="fr" if f.key == LANGUAGE else "other-model" if f.key == LOCAL else f.value
        )
        for f in gateway.fields
    ]

    presenter.on_settings_changed((LANGUAGE, LOCAL))

    state = presenter.state
    assert state.field(LOCAL).value == "other-model"
    assert state.field(LANGUAGE).value == "es"  # the user's text stays
    assert state.field(LANGUAGE).stale  # and it says it is not saved against the new value
    assert not state.field(LOCAL).stale
    presenter.edit(LANGUAGE, "de")
    assert not presenter.state.field(LANGUAGE).stale


def test_a_storage_failure_is_a_message_and_keeps_the_edits() -> None:
    presenter, gateway, _ = _loaded()
    gateway.replies["set_values"] = Err(ServiceError("SETTINGS_STORAGE_FAILED"))
    presenter.edit(LANGUAGE, "es")

    presenter.save()

    assert "could not be saved" in presenter.state.message
    assert presenter.state.can_save
    assert not presenter.state.saved


def test_an_apply_failure_after_a_save_stays_in_the_dialog() -> None:
    presenter, gateway, _ = _loaded()
    gateway.replies["set_values"] = Ok(SetOutcome((LANGUAGE,), (), True, ()))
    presenter.edit(LANGUAGE, "es")

    presenter.save()

    assert not presenter.state.saved
    assert "Restart the service" in presenter.state.message


def test_the_service_apply_problem_and_file_problem_are_shown() -> None:
    gateway = FakeSettingsGateway()
    gateway.fields = [replace(f, apply_problem="SETTINGS_APPLY_FAILED") for f in gateway.fields]
    presenter, _, _, _ = _wire(gateway)
    presenter.load()
    assert "could not be applied" in presenter.state.message

    gateway.fields = [
        replace(f, apply_problem="", file_problem="SETTINGS_FILE_INVALID") for f in gateway.fields
    ]
    presenter.load()
    assert "ignored" in presenter.state.message


# -- the API key row (BR-005): the key never passes through this presenter --


def test_the_key_row_reports_the_keyring_without_reading_the_key() -> None:
    cases = {
        KeyStatus.STORED: "Key stored",
        KeyStatus.MISSING: "No key stored",
        KeyStatus.LOCKED: "Keyring locked",
        KeyStatus.UNAVAILABLE: "Keyring unavailable",
    }
    for status, text in cases.items():
        keys = FakeApiKeyStore(status=Ok(status))
        presenter, _, _, _ = _wire(keys=keys)
        presenter.load()
        assert presenter.state.key_text == text
        assert presenter.state.can_remove_key is (status in {KeyStatus.STORED, KeyStatus.LOCKED})


def test_a_keyring_error_reads_as_unavailable() -> None:
    keys = FakeApiKeyStore(status=Err(ServiceError("KEYRING_UNAVAILABLE")))
    presenter, _, _, _ = _wire(keys=keys)
    presenter.load()
    assert presenter.state.key_status is KeyStatus.UNAVAILABLE


def test_removing_the_key_asks_the_store_then_refreshes_the_indicator() -> None:
    keys = FakeApiKeyStore(stored=["fake"])
    presenter, _, _, _ = _wire(keys=keys)
    presenter.load()
    assert presenter.state.key_text == "Key stored"

    presenter.remove_key()

    assert keys.removed == 1
    assert presenter.state.key_text == "No key stored"


def test_a_failed_removal_is_a_message_and_the_indicator_is_unchanged() -> None:
    keys = FakeApiKeyStore(
        stored=["fake"], remove_result=Err(ServiceError("KEYRING_REMOVE_FAILED"))
    )
    presenter, _, _, _ = _wire(keys=keys)
    presenter.load()
    presenter.remove_key()
    assert "could not be removed" in presenter.state.message
    assert presenter.state.key_text == "Key stored"


def test_a_state_never_holds_a_key() -> None:
    keys = FakeApiKeyStore(stored=["fake-secret-value"])
    presenter, _, _, states = _wire(keys=keys)
    presenter.load()
    assert all("fake-secret-value" not in repr(state) for state in states)
