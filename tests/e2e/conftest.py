"""Private session bus for D-Bus e2e tests. The user's real session bus is never touched."""

import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from meeting_scribe.preferences.domain.fields import FIELD_REGISTRY

_CONFIG_ENV = (
    *(f"{spec.section}_{spec.name}".upper() for spec in FIELD_REGISTRY),
    "SUMMARIES_CLOUD_ENABLED",
)

_CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:tmpdir={tmpdir}</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""


@dataclass(frozen=True)
class PrivateBus:
    address: str
    daemon: subprocess.Popen[str]


@pytest.fixture
def private_bus(tmp_path: Path) -> Iterator[PrivateBus]:
    """A throwaway ``dbus-daemon``; it is killed by PID at teardown."""
    config = tmp_path / "bus.conf"
    config.write_text(_CONFIG.format(tmpdir=tmp_path))
    daemon = subprocess.Popen(  # noqa: S603
        ["dbus-daemon", f"--config-file={config}", "--nofork", "--print-address=1"],  # noqa: S607
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert daemon.stdout is not None
        address = daemon.stdout.readline().strip()
        assert address.startswith("unix:"), "private dbus-daemon did not print an address"
        yield PrivateBus(address=address, daemon=daemon)
    finally:
        daemon.terminate()
        try:
            daemon.wait(timeout=5)
        except subprocess.TimeoutExpired:
            daemon.kill()
            daemon.wait()
        if daemon.stdout is not None:
            daemon.stdout.close()


@pytest.fixture
def bus_address(private_bus: PrivateBus) -> str:
    return private_bus.address


@pytest.fixture(autouse=True)
def _isolated_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Settings are loaded from an empty private XDG folder, never the user's real config.

    An empty ``.env`` there wins over a development ``./.env``, so nothing real is read.
    """
    xdg = tmp_path_factory.mktemp("xdg")
    (xdg / "meeting-scribe").mkdir()
    (xdg / "meeting-scribe" / ".env").write_text("", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    for name in _CONFIG_ENV:
        monkeypatch.delenv(name, raising=False)
