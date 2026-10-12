"""``Settings1`` end to end on a private bus (AC-1, AC-2, AC-7, AC-8, AC-9, AC-14).

The settings file lives under ``tmp_path``; systemd is a fake on the private bus; nothing touches
the real config folder, session bus or user systemd.
"""

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import NamedTuple

import pytest
from dbus_fast import DBusError
from dbus_fast.aio import MessageBus
from returns.result import Failure, Success

from meeting_scribe.preferences.domain.fields import FIELD_REGISTRY
from meeting_scribe.summaries.domain.errors import (
    SummaryCloudKeyLockedError,
    SummaryCloudKeyMissingError,
    SummaryError,
    SummaryModelsUnavailableError,
)
from meeting_scribe.summaries.domain.ports import ModelEntry
from tests.e2e.preferences.fake_systemd import FakeManager, start_fake_systemd
from tests.e2e.rig import Proxy, ServiceRig, connect, eventually, interface, running
from tests.unit.meetings.fakes import FakeKeyStore

pytestmark = pytest.mark.dbus

IFACE = "dev.meetingscribe.Settings1"
ERROR = "dev.meetingscribe.Error"
KEYS = [spec.key for spec in FIELD_REGISTRY]


class Session(NamedTuple):
    client: MessageBus
    settings: Proxy
    summaries: Proxy
    recording: Proxy
    props: Proxy


def _run(rig: ServiceRig, address: str, scenario: Callable[[Session], Awaitable[None]]) -> None:
    async def main() -> None:
        async with running(rig, address):
            client = await connect(address)
            try:
                session = Session(
                    client,
                    await interface(client, IFACE),
                    await interface(client, "dev.meetingscribe.Summaries1"),
                    await interface(client, "dev.meetingscribe.Recording1"),
                    await interface(client, "org.freedesktop.DBus.Properties"),
                )
                await scenario(session)
            finally:
                client.disconnect()

    asyncio.run(main())


def _plain(raw: dict) -> dict:
    return {key: variant.value for key, variant in raw.items()}


async def _error(call: Awaitable) -> DBusError:
    with pytest.raises(DBusError) as excinfo:
        await call
    return excinfo.value


def _file(rig: ServiceRig) -> dict:
    return json.loads(rig.settings_file.read_text(encoding="utf-8"))["values"]


def test_get_all(tmp_path: Path, bus_address: str) -> None:
    rig = ServiceRig(tmp_path, bus_address=bus_address)

    async def scenario(session: Session) -> None:
        rows = [_plain(row) for row in await session.settings.call_get_all()]
        assert [r["key"] for r in rows] == KEYS
        assert all(
            set(r)
            == {
                "key",
                "kind",
                "value",
                "default",
                "source",
                "locked",
                "lock_reason",
                "apply",
                "choices",
                "file_problem",
                "apply_problem",
            }
            for r in rows
        )
        by_key = {r["key"]: r for r in rows}
        provider = by_key["summaries.provider"]
        assert (provider["kind"], provider["choices"], provider["apply"]) == (
            "choice",
            ["local", "cloud"],
            "live",
        )
        assert (provider["value"], provider["source"], provider["locked"]) == (
            "cloud",
            "file",
            False,
        )
        folder = by_key["storage.meetings_dir"]
        assert (folder["kind"], folder["apply"], folder["value"]) == (
            "path",
            "restart",
            str(rig.meetings_dir),
        )
        language = by_key["summaries.language"]
        assert (language["source"], language["value"], language["default"]) == (
            "default",
            "auto",
            "auto",
        )
        assert all(r["file_problem"] == "" for r in rows)

    _run(rig, bus_address, scenario)


def test_get_all_reports_the_environment_lock(
    tmp_path: Path, bus_address: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUMMARIES_LOCAL_MODEL", "m-env")
    rig = ServiceRig(tmp_path, bus_address=bus_address)

    async def scenario(session: Session) -> None:
        row = {r["key"]: r for r in map(_plain, await session.settings.call_get_all())}[
            "summaries.local_model"
        ]
        assert (row["value"], row["source"], row["locked"]) == ("m-env", "env", True)

    _run(rig, bus_address, scenario)


def test_set_all_or_nothing(tmp_path: Path, bus_address: str) -> None:
    rig = ServiceRig(tmp_path, bus_address=bus_address)
    before = rig.settings_file.read_bytes()
    signals: list[tuple[list[str], dict]] = []

    async def scenario(session: Session) -> None:
        session.settings.on_settings_changed(lambda keys: signals.append((keys, _file(rig))))

        bad = _plain(
            await session.settings.call_set(
                {"summaries.local_model": "m-new", "summaries.language": "Spanish"}
            )
        )
        assert bad["applied"] == []
        assert [(k, c) for k, c, _m in bad["errors"]] == [("summaries.language", "FIELD_INVALID")]
        assert "Spanish" not in bad["errors"][0][2]
        assert rig.settings_file.read_bytes() == before
        await asyncio.sleep(0.2)
        assert signals == []

        unknown = _plain(
            await session.settings.call_set({"summaries.cloud_base_url": "https://x.example"})
        )
        assert [(k, c) for k, c, _m in unknown["errors"]] == [
            ("summaries.cloud_base_url", "FIELD_UNKNOWN")
        ]

        good = _plain(
            await session.settings.call_set(
                {"summaries.local_model": "m-new", "summaries.language": "es"}
            )
        )
        assert (good["applied"], good["errors"], good["restart_required"]) == (
            ["summaries.local_model", "summaries.language"],
            [],
            [],
        )
        assert good["apply_failed"] is False
        noop = _plain(await session.settings.call_set({}))
        assert (noop["applied"], noop["errors"], noop["apply_failed"]) == ([], [], False)
        await eventually(lambda: bool(signals), "SettingsChanged")
        keys, saved_at_signal = signals[0]
        assert keys == ["summaries.local_model", "summaries.language"]
        assert saved_at_signal["summaries.local_model"] == "m-new"  # saved before the signal
        rows = {r["key"]: r for r in map(_plain, await session.settings.call_get_all())}
        assert rows["summaries.local_model"]["value"] == "m-new"
        assert rows["summaries.local_model"]["source"] == "file"
        # live: the next Providers call shows the new model, no restart happened
        providers = [_plain(row) for row in await session.summaries.call_providers()]
        assert providers[0]["label"] == "Local (m-new)"

    _run(rig, bus_address, scenario)
    assert _file(rig)["summaries.language"] == "es"
    assert oct(rig.settings_file.stat().st_mode & 0o777) == "0o600"


def test_set_rejects_a_locked_field_with_a_row(
    tmp_path: Path, bus_address: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUMMARIES_LOCAL_MODEL", "m-env")
    rig = ServiceRig(tmp_path, bus_address=bus_address)

    async def scenario(session: Session) -> None:
        reply = _plain(
            await session.settings.call_set(
                {"summaries.local_model": "x", "summaries.language": "es"}
            )
        )
        assert reply["applied"] == []
        assert [(k, c) for k, c, _m in reply["errors"]] == [
            ("summaries.local_model", "FIELD_LOCKED")
        ]

    _run(rig, bus_address, scenario)


def test_the_meetings_folder_is_saved_and_needs_a_restart(tmp_path: Path, bus_address: str) -> None:
    rig = ServiceRig(tmp_path, bus_address=bus_address)
    new = tmp_path / "another-meetings"
    changes: list[dict] = []

    async def scenario(session: Session) -> None:
        session.props.on_properties_changed(lambda _i, changed, _x: changes.append(changed))
        assert (await session.props.call_get(IFACE, "RestartRequired")).value == []
        reply = _plain(await session.settings.call_set({"storage.meetings_dir": str(new)}))
        assert reply["restart_required"] == ["storage.meetings_dir"]
        assert (await session.props.call_get(IFACE, "RestartRequired")).value == [
            "storage.meetings_dir"
        ]
        await eventually(lambda: bool(changes), "PropertiesChanged")
        assert changes[0]["RestartRequired"].value == ["storage.meetings_dir"]
        assert new.is_dir()
        bad = _plain(await session.settings.call_set({"storage.meetings_dir": "relative"}))
        assert [(k, c) for k, c, _m in bad["errors"]] == [("storage.meetings_dir", "FIELD_INVALID")]
        assert "relative" not in bad["errors"][0][2]

    _run(rig, bus_address, scenario)
    assert _file(rig)["storage.meetings_dir"] == str(new)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_a_storage_failure_is_a_dbus_error_and_changes_nothing(
    tmp_path: Path, bus_address: str
) -> None:
    rig = ServiceRig(tmp_path, bus_address=bus_address)

    async def scenario(session: Session) -> None:
        rig.settings_file.parent.chmod(0o500)
        try:
            error = await _error(session.settings.call_set({"summaries.language": "es"}))
        finally:
            rig.settings_file.parent.chmod(0o700)
        assert error.type == f"{ERROR}.SETTINGS_STORAGE_FAILED"
        rows = {r["key"]: r for r in map(_plain, await session.settings.call_get_all())}
        assert rows["summaries.language"]["value"] == "auto"

    _run(rig, bus_address, scenario)


def test_restart_refused_while_busy(tmp_path: Path, bus_address: str) -> None:
    rig = ServiceRig(tmp_path, bus_address=bus_address)

    async def scenario(session: Session) -> None:
        await session.recording.call_start()  # the fake capture records nothing
        try:
            error = await _error(session.settings.call_restart())
            assert error.type == f"{ERROR}.SETTINGS_RESTART_BUSY"
        finally:
            await session.recording.call_stop()

    systemd: list[FakeManager] = []

    async def main() -> None:
        bus, manager = await start_fake_systemd(bus_address)
        systemd.append(manager)
        try:
            async with running(rig, bus_address):
                client = await connect(bus_address)
                try:
                    session = Session(
                        client,
                        await interface(client, IFACE),
                        await interface(client, "dev.meetingscribe.Summaries1"),
                        await interface(client, "dev.meetingscribe.Recording1"),
                        await interface(client, "org.freedesktop.DBus.Properties"),
                    )
                    await scenario(session)
                finally:
                    client.disconnect()
        finally:
            bus.disconnect()

    asyncio.run(main())
    assert systemd[0].restarts == []


def test_an_idle_restart_asks_systemd_after_the_reply(tmp_path: Path, bus_address: str) -> None:
    rig = ServiceRig(tmp_path, bus_address=bus_address)
    manager: list[FakeManager] = []

    async def main() -> None:
        bus, fake = await start_fake_systemd(bus_address)
        manager.append(fake)
        try:
            async with running(rig, bus_address):
                client = await connect(bus_address)
                try:
                    settings = await interface(client, IFACE)
                    assert await settings.call_restart() == "RESTARTING"
                    await eventually(lambda: bool(fake.restarts), "RestartUnit")
                finally:
                    client.disconnect()
        finally:
            bus.disconnect()

    asyncio.run(main())
    assert manager[0].restarts == [("meeting-scribe.service", "replace")]


def test_restart_without_systemd_is_unavailable(tmp_path: Path, bus_address: str) -> None:
    rig = ServiceRig(tmp_path, bus_address=bus_address)

    async def scenario(session: Session) -> None:
        error = await _error(session.settings.call_restart())
        assert error.type == f"{ERROR}.SETTINGS_RESTART_UNAVAILABLE"
        assert "systemctl --user restart meeting-scribe.service" in error.text

    _run(rig, bus_address, scenario)


def _models(reply: dict) -> tuple[str, list[list]]:
    plain = _plain(reply)
    return plain["status"], [list(row) for row in plain["models"]]


def test_list_models(tmp_path: Path, bus_address: str) -> None:
    rig = ServiceRig(tmp_path, bus_address=bus_address)
    rig.local_models.result = Success([ModelEntry(id="llama3:8b")])
    rig.cloud_models.result = Success(
        [
            ModelEntry(id="glm-5"),
            ModelEntry(id="gpt-5", supported=False, reason="NOT_SUPPORTED_YET"),
        ]
    )

    async def scenario(session: Session) -> None:
        local = await session.settings.call_list_models("local")
        assert _models(local) == ("OK", [["llama3:8b", True, ""]])
        cloud = await session.settings.call_list_models("cloud")
        assert _models(cloud) == (
            "OK",
            [["glm-5", True, ""], ["gpt-5", False, "NOT_SUPPORTED_YET"]],
        )
        assert FakeKeyStore.FAKE_KEY not in repr(cloud)  # BR-005: the key never crosses D-Bus

    _run(rig, bus_address, scenario)


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        (SummaryCloudKeyMissingError("x"), "SUMMARY_CLOUD_KEY_MISSING"),
        (SummaryCloudKeyLockedError("x"), "SUMMARY_CLOUD_KEY_LOCKED"),
    ],
)
def test_the_cloud_list_without_a_usable_key_is_a_status(
    tmp_path: Path, bus_address: str, failure: SummaryError, status: str
) -> None:
    rig = ServiceRig(tmp_path, bus_address=bus_address)
    rig.keys.failure = failure

    async def scenario(session: Session) -> None:
        assert _models(await session.settings.call_list_models("cloud")) == (status, [])
        assert rig.cloud_models.asks == 0

    _run(rig, bus_address, scenario)


def test_an_unavailable_or_unknown_list_is_a_status_not_an_error(
    tmp_path: Path, bus_address: str
) -> None:
    rig = ServiceRig(tmp_path, bus_address=bus_address)
    rig.local_models.result = Failure(SummaryModelsUnavailableError("down"))

    async def scenario(session: Session) -> None:
        local = await session.settings.call_list_models("local")
        assert _models(local) == ("SUMMARY_MODELS_UNAVAILABLE", [])
        other = await session.settings.call_list_models("elsewhere")
        assert _models(other) == ("SUMMARY_MODELS_UNAVAILABLE", [])

    _run(rig, bus_address, scenario)


def test_set_checks_a_changed_model_against_the_available_list(
    tmp_path: Path, bus_address: str
) -> None:
    rig = ServiceRig(tmp_path, bus_address=bus_address)
    rig.cloud_models.result = Success(
        [
            ModelEntry(id="glm-5"),
            ModelEntry(id="gpt-5", supported=False, reason="NOT_SUPPORTED_YET"),
        ]
    )
    before = rig.settings_file.read_bytes()

    async def scenario(session: Session) -> None:
        rejected = _plain(await session.settings.call_set({"summaries.cloud_model": "gpt-5"}))
        assert rejected["applied"] == []
        assert [row[:2] for row in rejected["errors"]] == [
            ("summaries.cloud_model", "FIELD_INVALID")
        ]
        assert rig.settings_file.read_bytes() == before
        saved = _plain(await session.settings.call_set({"summaries.cloud_model": "glm-5"}))
        assert saved["applied"] == ["summaries.cloud_model"]

    _run(rig, bus_address, scenario)
    assert _file(rig)["summaries.cloud_model"] == "glm-5"


def test_set_accepts_a_model_while_the_list_is_unavailable(
    tmp_path: Path, bus_address: str
) -> None:
    rig = ServiceRig(tmp_path, bus_address=bus_address)
    rig.keys.failure = SummaryCloudKeyMissingError("x")

    async def scenario(session: Session) -> None:
        saved = _plain(await session.settings.call_set({"summaries.cloud_model": "anything-1"}))
        assert saved["applied"] == ["summaries.cloud_model"]

    _run(rig, bus_address, scenario)
