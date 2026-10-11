"""Error names map to user messages with a next step, never a stack trace (AC-15)."""

import re
from pathlib import Path

from meeting_scribe_review.core.constants import METHODS, SUMMARY_METHODS
from meeting_scribe_review.core.errors import (
    INVALID_REPLY,
    RENAME_CALL,
    SERVICE_NOT_RUNNING,
    SERVICE_OUTDATED,
    ServiceError,
    error_from_dbus_name,
    message_for,
)

ROOT = Path(__file__).resolve().parents[3]
API_CONTRACTS = ROOT / "docs" / "architecture" / "api-contracts.md"
CATALOG = ROOT / "docs" / "architecture" / "error-codes-catalog.md"
CALLS = ("Get", *METHODS, *SUMMARY_METHODS)


def _codes_a_review_call_can_return() -> set[str]:
    codes: set[str] = set()
    for line in API_CONTRACTS.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 5 and cells[0] in {f"`{call}`" for call in CALLS}:
            codes.update(re.findall(r"`([A-Z][A-Z_]+)`", cells[-1]))
    return codes & _catalog_codes()  # drops prose such as the state `DONE`


def _catalog_codes() -> set[str]:
    """Codes in the second column of the catalog tables (the ``error_code`` column)."""
    return set(
        re.findall(
            r"^\|[^|]*\|\s*`([A-Z][A-Z_]+)`\s*\|", CATALOG.read_text(encoding="utf-8"), re.MULTILINE
        )
    )


def test_every_code_a_review_call_can_return_has_a_message() -> None:
    codes = _codes_a_review_call_can_return()
    assert {
        "SPEAKER_NAME_TAKEN",
        "MEETING_NOT_PROCESSED",
        "ENTITY_NOT_FOUND_SPEAKER_SAMPLE",
    } <= codes
    for code in sorted(codes):
        text = message_for(ServiceError(code)).text
        assert code not in text, f"{code} has no specific message in core/errors.py"


def test_every_summary_code_of_the_catalog_has_a_message() -> None:
    codes = {code for code in _catalog_codes() if code.startswith("SUMMARY_")}
    assert {"SUMMARY_CLOUD_KEY_MISSING", "SUMMARY_TIMEOUT", "SUMMARY_FAILED"} <= codes
    for code in sorted(codes):
        text = message_for(ServiceError(code)).text
        assert code not in text, f"{code} has no specific message in core/errors.py"


def test_every_summary_call_code_has_a_message() -> None:
    codes = _codes_a_review_call_can_return() | {"ENTITY_NOT_FOUND_SUMMARY"}
    assert {"SUMMARY_GPU_BUSY", "SUMMARY_CLOUD_NOT_CONFIRMED", "SUMMARY_STORAGE_FAILED"} <= codes
    for code in sorted(codes):
        assert code not in message_for(ServiceError(code)).text, code


def test_key_errors_say_what_to_do_without_a_command_for_the_service() -> None:
    missing = message_for(ServiceError("SUMMARY_CLOUD_KEY_MISSING"))
    assert "API key" in missing.text
    assert "unlock" in message_for(ServiceError("SUMMARY_CLOUD_KEY_LOCKED")).text.lower()


def test_invalid_value_is_worded_for_the_call_that_failed() -> None:
    error = ServiceError("INVALID_VALUE")
    assert message_for(error, call=RENAME_CALL).text == "That name is not valid."
    assert message_for(error, call="merge_speakers").text == "The request was not valid."
    assert message_for(error).text == "The request was not valid."


def test_service_error_names_become_codes() -> None:
    assert error_from_dbus_name("dev.meetingscribe.Error.SPEAKER_NAME_TAKEN") == ServiceError(
        "SPEAKER_NAME_TAKEN"
    )


def test_unknown_method_means_the_service_is_older_than_the_app() -> None:
    error = error_from_dbus_name("org.freedesktop.DBus.Error.UnknownMethod")
    assert error == ServiceError(SERVICE_OUTDATED)
    assert "restart" in message_for(error).text.lower()


def test_missing_service_names_mean_the_service_is_not_running() -> None:
    for name in (
        "org.freedesktop.DBus.Error.ServiceUnknown",
        "org.freedesktop.DBus.Error.NameHasNoOwner",
        "org.freedesktop.DBus.Error.Spawn.ChildExited",
    ):
        assert error_from_dbus_name(name) == ServiceError(SERVICE_NOT_RUNNING)


def test_not_running_message_gives_the_command_to_start_the_service() -> None:
    message = message_for(ServiceError(SERVICE_NOT_RUNNING))
    assert message.text == "The Meeting Scribe service is not running"
    assert message.hint == "systemctl --user start meeting-scribe.service"


def test_an_unmapped_service_code_gets_a_generic_message_with_the_code() -> None:
    message = message_for(error_from_dbus_name("dev.meetingscribe.Error.SOMETHING_NEW"))
    assert "SOMETHING_NEW" in message.text


def test_a_foreign_error_name_gets_a_generic_message_without_leaking_it() -> None:
    error = error_from_dbus_name("org.example.Whatever")
    assert "org.example" not in message_for(error).text


def test_a_damaged_meeting_says_to_reprocess_it() -> None:
    assert message_for(ServiceError("MEETING_CORRUPT")).text == (
        "This meeting's transcript is damaged. Reprocess it."
    )


def test_invalid_reply_has_a_message() -> None:
    assert INVALID_REPLY not in message_for(ServiceError(INVALID_REPLY)).text
