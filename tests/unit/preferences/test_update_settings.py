"""``Set``: validate everything first, apply nothing on any error (AC-2, AC-7, AC-14)."""

import logging
import threading
from pathlib import Path

import pytest
from returns.result import Failure

from tests.unit.preferences.fakes import Service


def test_valid_values_are_written_applied_and_signalled_after_the_save(tmp_path: Path) -> None:
    fx = Service(tmp_path)

    outcome = fx.service.set(
        {"summaries.provider": "cloud", "summaries.local_model": "m-new"}
    ).unwrap()

    assert outcome.errors == ()
    assert outcome.applied == ("summaries.provider", "summaries.local_model")
    assert fx.store.values == {"summaries.provider": "cloud", "summaries.local_model": "m-new"}
    assert fx.events.log == [("changed", ("summaries.provider", "summaries.local_model"))]
    assert fx.events.writes_seen == [1]  # the signal came after the file was saved
    assert [s.summaries.local_model for s in fx.applied] == ["m-new"]
    assert fx.service.get_all()[0].value == "cloud"  # the next GetAll sees it


def test_one_invalid_field_applies_nothing(tmp_path: Path) -> None:
    fx = Service(tmp_path, {"summaries.language": "es"})

    outcome = fx.service.set(
        {
            "summaries.provider": "cloud",
            "summaries.language": "Spanish",
            "summaries.local_model": "m",
        }
    ).unwrap()

    assert outcome.applied == ()
    assert [(e.key, e.code) for e in outcome.errors] == [("summaries.language", "FIELD_INVALID")]
    assert fx.store.writes == []
    assert fx.events.log == []
    assert fx.applied == []
    assert fx.store.values == {"summaries.language": "es"}
    assert fx.service.get_all()[0].value == "local"  # still the old effective value


def test_unknown_and_locked_keys_are_rows_not_exceptions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SUMMARIES_LOCAL_MODEL", "m-env")
    fx = Service(tmp_path)

    outcome = fx.service.set(
        {
            "summaries.cloud_base_url": "https://x.example",
            "summaries.local_model": "m",
            "summaries.language": "es",
        }
    ).unwrap()

    assert outcome.applied == ()
    assert {(e.key, e.code) for e in outcome.errors} == {
        ("summaries.cloud_base_url", "FIELD_UNKNOWN"),
        ("summaries.local_model", "FIELD_LOCKED"),
    }
    assert fx.store.writes == []


def test_a_value_equal_to_the_default_is_removed_from_the_file(tmp_path: Path) -> None:
    fx = Service(tmp_path, {"summaries.language": "es", "summaries.cloud_model": "glm"})

    fx.service.set({"summaries.language": "auto"}).unwrap()

    assert fx.store.values == {"summaries.cloud_model": "glm"}
    assert fx.service.get_all()[2].value == "auto"


def test_the_meetings_folder_needs_a_restart_and_nothing_is_moved(tmp_path: Path) -> None:
    old = tmp_path / "old"
    (old / "m1").mkdir(parents=True)
    (old / "m1" / "meeting.json").write_text("{}", encoding="utf-8")
    fx = Service(tmp_path, {"storage.meetings_dir": str(old)})
    new = tmp_path / "new-meetings"

    outcome = fx.service.set({"storage.meetings_dir": str(new)}).unwrap()

    assert outcome.applied == ("storage.meetings_dir",)
    assert outcome.restart_required == ("storage.meetings_dir",)
    assert fx.service.restart_required == ("storage.meetings_dir",)
    assert ("restart_required", ("storage.meetings_dir",)) in fx.events.log
    assert new.is_dir()
    assert sorted(p.name for p in old.iterdir()) == ["m1"]  # not moved, copied or deleted
    assert (old / "m1" / "meeting.json").read_text(encoding="utf-8") == "{}"
    assert fx.store.values == {"storage.meetings_dir": str(new)}


def test_changing_the_folder_back_clears_the_pending_restart(tmp_path: Path) -> None:
    running = tmp_path / "running"
    running.mkdir()
    fx = Service(tmp_path, {"storage.meetings_dir": str(running)})
    fx.service.set({"storage.meetings_dir": str(tmp_path / "other")}).unwrap()
    assert fx.service.restart_required == ("storage.meetings_dir",)

    fx.service.set({"storage.meetings_dir": str(running)}).unwrap()

    assert fx.service.restart_required == ()
    assert ("restart_required", ()) in fx.events.log


def test_an_invalid_folder_is_a_field_row_and_nothing_is_saved(tmp_path: Path) -> None:
    fx = Service(tmp_path)
    file = tmp_path / "a-file"
    file.write_text("x", encoding="utf-8")

    outcome = fx.service.set(
        {"storage.meetings_dir": str(file), "summaries.language": "es"}
    ).unwrap()

    assert [(e.key, e.code) for e in outcome.errors] == [("storage.meetings_dir", "FIELD_INVALID")]
    assert outcome.applied == ()
    assert fx.store.writes == []

    relative = fx.service.set({"storage.meetings_dir": "relative/dir"}).unwrap()
    assert [e.code for e in relative.errors] == ["FIELD_INVALID"]


def test_a_storage_failure_changes_nothing(tmp_path: Path) -> None:
    fx = Service(tmp_path)
    fx.store.fail = True

    result = fx.service.set({"summaries.language": "es"})

    assert isinstance(result, Failure)
    assert result.failure().error_code == "SETTINGS_STORAGE_FAILED"
    assert fx.events.log == []
    assert fx.applied == []
    assert fx.service.get_all()[2].value == "auto"


def test_two_sets_never_overlap(tmp_path: Path) -> None:
    fx = Service(tmp_path)
    fx.store.gate = threading.Event()
    first = threading.Thread(target=fx.service.set, args=({"summaries.language": "es"},))
    second = threading.Thread(target=fx.service.set, args=({"summaries.language": "fr"},))

    first.start()
    assert fx.store.entered.wait(5)
    second.start()
    second.join(0.3)

    assert second.is_alive()  # waits for the first one
    assert fx.store.write_calls == ["es"]  # the second never entered write while the first held it
    fx.store.gate.set()
    first.join(5)
    second.join(5)
    assert fx.store.write_calls == ["es", "fr"]
    assert fx.store.max_concurrent == 1


def test_an_empty_set_is_a_no_op_that_does_not_rewrite_the_file(tmp_path: Path) -> None:
    fx = Service(tmp_path, {"summaries.language": "es"})

    outcome = fx.service.set({}).unwrap()

    assert (outcome.applied, outcome.errors, outcome.apply_failed) == ((), (), False)
    assert fx.store.writes == []
    assert fx.events.log == []
    assert fx.applied == []


def test_the_folder_is_not_created_when_another_field_is_invalid(tmp_path: Path) -> None:
    fx = Service(tmp_path)
    new = tmp_path / "never-created"

    outcome = fx.service.set(
        {"storage.meetings_dir": str(new), "summaries.language": "Not Valid"}
    ).unwrap()

    assert [e.key for e in outcome.errors] == ["summaries.language"]
    assert not new.exists()
    assert fx.store.writes == []


def test_a_failing_live_apply_still_saves_signals_and_tells_the_client(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    fx = Service(tmp_path)
    fx.fail_apply = True
    new = tmp_path / "other-meetings"

    with caplog.at_level(logging.DEBUG):
        outcome = fx.service.set(
            {"summaries.local_model": "model-private", "storage.meetings_dir": str(new)}
        ).unwrap()

    assert outcome.apply_failed is True
    assert outcome.errors == ()
    assert outcome.applied == ("summaries.local_model", "storage.meetings_dir")
    assert outcome.restart_required == ("storage.meetings_dir",)
    assert fx.store.values["summaries.local_model"] == "model-private"  # saved
    assert fx.service.restart_required == ("storage.meetings_dir",)
    assert fx.events.log[0] == ("changed", ("summaries.local_model", "storage.meetings_dir"))
    assert ("restart_required", ("storage.meetings_dir",)) in fx.events.log
    assert fx.service.get_all()[1].value == "model-private"
    assert "RuntimeError" in caplog.text
    assert "boom" not in caplog.text
    assert "model-private" not in caplog.text


def test_logs_keys_not_values(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    fx = Service(tmp_path)
    secret_path = tmp_path / "private-person-folder"

    with caplog.at_level(logging.DEBUG):
        fx.service.set(
            {"storage.meetings_dir": str(secret_path), "summaries.local_model": "model-xyz"}
        ).unwrap()
        fx.service.set({"summaries.language": "Private Value"}).unwrap()

    assert "storage.meetings_dir" in caplog.text
    for value in ("private-person-folder", "model-xyz", "Private Value"):
        assert value not in caplog.text


def test_a_failed_save_removes_the_folder_it_just_created(tmp_path: Path) -> None:
    fx = Service(tmp_path)
    fx.store.fail = True
    new = tmp_path / "fresh-meetings"

    result = fx.service.set({"storage.meetings_dir": str(new)})

    assert isinstance(result, Failure)
    assert not new.exists()


def test_a_failed_save_keeps_a_folder_that_already_existed(tmp_path: Path) -> None:
    fx = Service(tmp_path)
    fx.store.fail = True
    existing = tmp_path / "old-meetings"
    existing.mkdir()

    result = fx.service.set({"storage.meetings_dir": str(existing)})

    assert isinstance(result, Failure)
    assert existing.is_dir()


def test_a_failed_save_never_removes_a_folder_that_is_no_longer_empty(tmp_path: Path) -> None:
    fx = Service(tmp_path)
    new = tmp_path / "raced-meetings"

    def intrude() -> None:
        (new / "other-file").write_bytes(b"")

    fx.store.on_write = intrude
    fx.store.fail = True

    fx.service.set({"storage.meetings_dir": str(new)})

    assert (new / "other-file").exists()


def test_an_apply_failure_stays_visible_until_a_later_apply_succeeds(tmp_path: Path) -> None:
    fx = Service(tmp_path)
    assert {row.apply_problem for row in fx.service.get_all()} == {""}
    fx.fail_apply = True

    fx.service.set({"summaries.local_model": "model-a"})

    assert {row.apply_problem for row in fx.service.get_all()} == {"SETTINGS_APPLY_FAILED"}
    fx.fail_apply = False

    fx.service.set({"summaries.local_model": "model-b"})

    assert {row.apply_problem for row in fx.service.get_all()} == {""}
