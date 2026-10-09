"""Private session bus for D-Bus e2e tests. The user's real session bus is never touched."""

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

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


@pytest.fixture
def bus_address(tmp_path: Path) -> Iterator[str]:
    """Address of a throwaway ``dbus-daemon``; it is killed by PID at teardown."""
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
        yield address
    finally:
        daemon.terminate()
        try:
            daemon.wait(timeout=5)
        except subprocess.TimeoutExpired:
            daemon.kill()
            daemon.wait()
        if daemon.stdout is not None:
            daemon.stdout.close()
