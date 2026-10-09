import configparser
from pathlib import Path

import pytest

from meeting_scribe.base.infrastructure.dbus.constants import BUS_NAME
from meeting_scribe.main import EXIT_NAME_TAKEN

PACKAGING = Path(__file__).resolve().parents[2] / "packaging"
UNIT_NAME = "meeting-scribe.service"
UNIT_TEMPLATE = PACKAGING / "systemd" / f"{UNIT_NAME}.in"
ACTIVATION_FILE = PACKAGING / "dbus" / f"{BUS_NAME}.service"
PLACEHOLDER = "@EXEC_START@"


def _parse(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str  # type: ignore[assignment,method-assign]  # keys are case-sensitive
    parser.read_string(path.read_text(encoding="utf-8"))
    return parser


@pytest.fixture(name="unit")
def _unit() -> configparser.ConfigParser:
    return _parse(UNIT_TEMPLATE)


@pytest.fixture(name="activation")
def _activation() -> configparser.ConfigParser:
    return _parse(ACTIVATION_FILE)


def test_unit_declares_the_bus_name_from_code(unit: configparser.ConfigParser) -> None:
    assert unit["Service"]["Type"] == "dbus"
    assert unit["Service"]["BusName"] == BUS_NAME


def test_unit_exec_start_is_a_single_placeholder() -> None:
    text = UNIT_TEMPLATE.read_text(encoding="utf-8")
    assert text.count(PLACEHOLDER) == 1
    exec_lines = [ln for ln in text.splitlines() if ln.startswith("ExecStart=")]
    assert exec_lines == [f"ExecStart={PLACEHOLDER}"]


def test_unit_does_not_restart_when_the_bus_name_is_taken(
    unit: configparser.ConfigParser,
) -> None:
    prevented = unit["Service"]["RestartPreventExitStatus"].split()
    assert str(EXIT_NAME_TAKEN) in prevented
    assert unit["Service"]["Restart"] == "on-failure"
    assert unit["Service"]["RestartSec"]


def test_unit_orders_after_the_pipewire_user_services(unit: configparser.ConfigParser) -> None:
    for key in ("After", "Wants"):
        assert {"pipewire.service", "pipewire-pulse.service"} <= set(unit["Unit"][key].split())


def test_unit_is_wanted_by_the_default_target(unit: configparser.ConfigParser) -> None:
    assert unit["Install"]["WantedBy"] == "default.target"


def test_unit_carries_no_secrets_or_environment_values() -> None:
    text = UNIT_TEMPLATE.read_text(encoding="utf-8").lower()
    for forbidden in ("environment=", "environmentfile=", "token", "hf_"):
        assert forbidden not in text


def test_activation_file_names_the_bus_name_from_code(
    activation: configparser.ConfigParser,
) -> None:
    assert ACTIVATION_FILE.name == f"{BUS_NAME}.service"
    assert activation["D-BUS Service"]["Name"] == BUS_NAME


def test_activation_goes_through_the_systemd_unit(activation: configparser.ConfigParser) -> None:
    section = activation["D-BUS Service"]
    assert section["SystemdService"] == UNIT_NAME
    assert section["Exec"] == "/bin/false"


def test_unit_lets_the_service_stop_ffmpeg_itself(unit: configparser.ConfigParser) -> None:
    # Default control-group mode would SIGTERM ffmpeg alongside the service, racing the
    # graceful `q` stop; `mixed` signals only the main process first.
    assert unit["Service"]["KillMode"] == "mixed"
