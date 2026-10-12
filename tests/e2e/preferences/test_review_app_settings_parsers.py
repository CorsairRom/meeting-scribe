"""The app parsers accept the real ``Settings1`` replies (private bus, spec section 12).

The settings file lives under ``tmp_path``; nothing touches the real config, bus or keyring.
"""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from meeting_scribe_review.core.parsers import (
    parse_keys,
    parse_set_outcome,
    parse_settings_fields,
)
from meeting_scribe_review.core.result import Ok
from tests.e2e.rig import ServiceRig, connect, interface, running

pytestmark = pytest.mark.dbus


def _plain(value: Any) -> Any:
    """What ``Gio.DBusProxy`` hands the app: variants unwrapped, containers as lists and dicts."""
    if hasattr(value, "signature") and hasattr(value, "value"):
        return _plain(value.value)
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    return value


def test_parsers_accept_the_real_replies(tmp_path: Path, bus_address: str) -> None:
    rig = ServiceRig(tmp_path, bus_address=bus_address)

    async def scenario() -> None:
        async with running(rig, bus_address):
            client = await connect(bus_address)
            try:
                settings = await interface(client, "dev.meetingscribe.Settings1")
                props = await interface(client, "org.freedesktop.DBus.Properties")
                rows = parse_settings_fields(_plain(await settings.call_get_all()))
                assert isinstance(rows, Ok)
                assert rows.value[0].key == "summaries.provider"
                assert {r.apply_problem for r in rows.value} == {""}

                good = parse_set_outcome(
                    _plain(await settings.call_set({"summaries.language": "es"}))
                )
                assert isinstance(good, Ok) and good.value.applied == ("summaries.language",)
                bad = parse_set_outcome(
                    _plain(await settings.call_set({"summaries.language": "No"}))
                )
                assert isinstance(bad, Ok) and bad.value.errors[0].code == "FIELD_INVALID"

                pending = await props.call_get("dev.meetingscribe.Settings1", "RestartRequired")
                assert parse_keys(_plain(pending)) == Ok(())
            finally:
                client.disconnect()

    asyncio.run(scenario())
