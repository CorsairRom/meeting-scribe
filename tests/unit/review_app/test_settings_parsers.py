"""The ``Settings1`` replies become models; a malformed reply is INVALID_REPLY (AC-1, AC-2)."""

from typing import Any

import pytest

from meeting_scribe_review.core.errors import INVALID_REPLY, ServiceError
from meeting_scribe_review.core.models import FieldIssue, SetOutcome, SettingsField
from meeting_scribe_review.core.parsers import (
    parse_keys,
    parse_restarting,
    parse_set_outcome,
    parse_settings_fields,
)
from meeting_scribe_review.core.result import Err, Ok
from meeting_scribe_review.core.signals import dispatch_settings_signal

ROW: dict[str, Any] = {
    "key": "summaries.provider",
    "kind": "choice",
    "value": "local",
    "default": "local",
    "source": "default",
    "locked": False,
    "lock_reason": "",
    "apply": "live",
    "choices": ["local", "cloud"],
    "file_problem": "",
    "apply_problem": "",
}
INVALID = Err(ServiceError(INVALID_REPLY))


def test_a_row_becomes_a_settings_field() -> None:
    assert parse_settings_fields([ROW]) == Ok(
        [
            SettingsField(
                "summaries.provider",
                "choice",
                "local",
                "local",
                "default",
                False,
                "",
                "live",
                ("local", "cloud"),
                "",
                "",
            )
        ]
    )


@pytest.mark.parametrize("missing", sorted(ROW))
def test_a_row_without_a_member_is_invalid(missing: str) -> None:
    assert parse_settings_fields([{k: v for k, v in ROW.items() if k != missing}]) == INVALID


@pytest.mark.parametrize(
    "bad",
    [{"locked": "no"}, {"value": 3}, {"choices": "local"}, {"choices": [1]}],
)
def test_a_row_with_a_wrongly_typed_member_is_invalid(bad: dict[str, Any]) -> None:
    assert parse_settings_fields([{**ROW, **bad}]) == INVALID


def test_rows_must_be_a_list_of_mappings() -> None:
    assert parse_settings_fields("rows") == INVALID
    assert parse_settings_fields(["row"]) == INVALID
    assert parse_settings_fields([]) == Ok([])


def test_a_set_reply_becomes_an_outcome() -> None:
    reply = {
        "applied": [],
        "restart_required": ["storage.meetings_dir"],
        "apply_failed": False,
        "errors": [["summaries.language", "FIELD_INVALID", "Use a code."]],
    }
    assert parse_set_outcome(reply) == Ok(
        SetOutcome(
            (),
            ("storage.meetings_dir",),
            False,
            (FieldIssue("summaries.language", "FIELD_INVALID", "Use a code."),),
        )
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"errors": [["k", "FIELD_INVALID"]]},
        {"errors": "none"},
        {"apply_failed": 1},
        {"applied": "x"},
    ],
)
def test_a_malformed_set_reply_is_invalid(bad: dict[str, Any]) -> None:
    good = {"applied": [], "restart_required": [], "apply_failed": False, "errors": []}
    assert parse_set_outcome({**good, **bad}) == INVALID
    assert parse_set_outcome("nope") == INVALID


def test_keys_and_the_restart_word() -> None:
    assert parse_keys(["a", "b"]) == Ok(("a", "b"))
    assert parse_keys("a") == INVALID
    assert parse_restarting("RESTARTING") == Ok("RESTARTING")
    assert parse_restarting(1) == INVALID


class _Recorder:
    def __init__(self) -> None:
        self.keys: list[tuple[str, ...]] = []

    def on_service_available(self, available: bool) -> None:
        raise AssertionError(available)

    def on_settings_changed(self, keys: tuple[str, ...]) -> None:
        self.keys.append(keys)


def test_the_settings_signal_reaches_the_listener_and_a_malformed_one_is_dropped() -> None:
    rec = _Recorder()
    assert dispatch_settings_signal(rec, "SettingsChanged", (["summaries.language"],))
    assert not dispatch_settings_signal(rec, "SettingsChanged", ("summaries.language",))
    assert not dispatch_settings_signal(rec, "SettingsChanged", ([1],))
    assert not dispatch_settings_signal(rec, "Other", (["x"],))
    assert rec.keys == [("summaries.language",)]
