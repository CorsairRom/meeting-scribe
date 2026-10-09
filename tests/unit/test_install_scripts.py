"""Behavior of ``scripts/install.sh`` and ``scripts/uninstall.sh`` against fake system tools.

The scripts run in a throwaway HOME with a PATH that holds only fake ``uv``, ``systemctl``,
``gnome-extensions`` and ``gdbus`` plus symlinks to a whitelist of coreutils, so the real tools can
never run. Every fake appends its argv to a log file, which is also the proof the fakes were used.
"""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
INSTALL = SCRIPTS / "install.sh"
UNINSTALL = SCRIPTS / "uninstall.sh"
UNIT = "meeting-scribe.service"
ACTIVATION = "dev.meetingscribe.Service.service"
EXTENSION = "meeting-scribe@meetingscribe.dev"
COREUTILS = [
    "bash",
    "env",
    "mkdir",
    "chmod",
    "cp",
    "rm",
    "mktemp",
    "dirname",
    "basename",
    "cat",
    "sed",
    "grep",
    "tr",
    "head",
    "cut",
    "sort",
    "ln",
    "date",
    "mv",
    "touch",
]

FAKES = {
    "uv": """
case "$1 $2 $3" in
  "tool dir --bin") echo "$FAKE_BIN_DIR" ;;
  "tool install "*) mkdir -p "$FAKE_BIN_DIR"; : > "$FAKE_BIN_DIR/meeting-scribe"
                    chmod +x "$FAKE_BIN_DIR/meeting-scribe" ;;
esac
""",
    "systemctl": """
case "$*" in
  *is-active*) [ "${FAKE_UNIT_ACTIVE:-0}" = 1 ] && exit 0 || exit 3 ;;
esac
""",
    "gnome-extensions": """
case "$1" in
  info) [ "${FAKE_EXT_KNOWN:-1}" = 1 ] && echo "State: ENABLED" && exit 0 || exit 1 ;;
  enable) [ "${FAKE_EXT_ENABLE_FAIL:-0}" = 1 ] && exit 2 || exit 0 ;;
esac
""",
    "gdbus": """
case "$*" in
  *NameHasOwner*) [ "${FAKE_GDBUS_MODE:-noowner}" = noowner ] \\
                  && echo "(false,)" || echo "(true,)" ;;
  *Properties.Get*) [ "$FAKE_GDBUS_MODE" = recording ] && echo "(<'RECORDING'>,)" \
                    || echo "(<'IDLE'>,)" ;;
esac
""",
}


@dataclass
class Env:
    home: Path
    bin_dir: Path
    log: Path
    env: dict[str, str]

    @property
    def unit_path(self) -> Path:
        return self.home / ".config" / "systemd" / "user" / UNIT

    @property
    def activation_path(self) -> Path:
        return self.home / ".local" / "share" / "dbus-1" / "services" / ACTIVATION

    @property
    def exe(self) -> Path:
        return self.bin_dir / "meeting-scribe"

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def run(self, script: Path, **extra: str) -> subprocess.CompletedProcess[str]:
        env = {**self.env, **extra}
        return subprocess.run(  # noqa: S603  # fixed argv, sandboxed PATH
            [str(self.env["BASH"]), str(script)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )


@pytest.fixture(name="sandbox")
def _sandbox(tmp_path: Path) -> Env:
    home = tmp_path / "home"
    home.mkdir()
    fakes = tmp_path / "fakes"
    fakes.mkdir()
    for name in COREUTILS:
        found = shutil.which(name)
        if found:
            (fakes / name).symlink_to(found)
    log = tmp_path / "calls.log"
    for name, body in FAKES.items():
        path = fakes / name
        path.write_text(f'#!/usr/bin/env bash\necho "{name} $*" >> "$FAKE_LOG"\n{body}')
        path.chmod(0o755)
    bin_dir = tmp_path / "uvbin"
    env = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "PATH": str(fakes),
        "FAKE_LOG": str(log),
        "FAKE_BIN_DIR": str(bin_dir),
        "BASH": str(shutil.which("bash")),
    }
    return Env(home=home, bin_dir=bin_dir, log=log, env=env)


def _index(calls: list[str], prefix: str) -> int:
    return next(i for i, line in enumerate(calls) if line.startswith(prefix))


def test_fakes_are_the_only_tools_on_path(sandbox: Env) -> None:
    sandbox.run(INSTALL)
    assert any(line.startswith("uv ") for line in sandbox.calls())
    assert sandbox.env["PATH"] == str(sandbox.log.parent / "fakes")  # nothing else is reachable


def test_install_uses_a_python_312_uv_tool_install_of_the_repo(sandbox: Env) -> None:
    result = sandbox.run(INSTALL)
    assert result.returncode == 0, result.stderr
    expected = f"uv tool install --force --reinstall --python 3.12 {ROOT}"
    assert expected in sandbox.calls()


def test_install_renders_the_unit_with_the_absolute_exec_start(sandbox: Env) -> None:
    sandbox.run(INSTALL)
    text = sandbox.unit_path.read_text()
    assert f"ExecStart={sandbox.exe}\n" in text
    assert "@EXEC_START@" not in text


def test_install_copies_the_activation_file(sandbox: Env) -> None:
    sandbox.run(INSTALL)
    source = ROOT / "packaging" / "dbus" / ACTIVATION
    assert sandbox.activation_path.read_text() == source.read_text()


def test_first_install_reloads_then_enables_and_starts(sandbox: Env) -> None:
    sandbox.run(INSTALL)
    calls = sandbox.calls()
    reload_at = _index(calls, "systemctl --user daemon-reload")
    enable_at = _index(calls, f"systemctl --user enable --now {UNIT}")
    assert reload_at < enable_at
    assert not any("restart" in line for line in calls)


def test_reinstall_restarts_the_running_service_when_idle(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="idle")
    calls = sandbox.calls()
    assert result.returncode == 0, result.stderr
    assert f"systemctl --user restart {UNIT}" in calls
    assert _index(calls, "systemctl --user daemon-reload") < _index(
        calls, "systemctl --user restart"
    )


def test_reinstall_never_restarts_while_recording(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="recording")
    assert result.returncode == 0, result.stderr
    assert not any("restart" in line for line in sandbox.calls())
    assert "stop the recording" in (result.stdout + result.stderr).lower()
    assert sandbox.unit_path.exists()  # the rest of the install still ran
    assert any(line.startswith("gnome-extensions install --force") for line in sandbox.calls())


def test_install_packs_installs_and_enables_the_extension(sandbox: Env) -> None:
    result = sandbox.run(INSTALL)
    calls = sandbox.calls()
    assert result.returncode == 0, result.stderr
    pack = _index(calls, "gnome-extensions pack")
    install = _index(calls, "gnome-extensions install --force")
    enable = _index(calls, f"gnome-extensions enable {EXTENSION}")
    assert pack < install < enable
    assert f"{EXTENSION}.shell-extension.zip" in calls[install]


def test_wayland_log_out_hint_when_enable_fails(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_EXT_ENABLE_FAIL="1", FAKE_EXT_KNOWN="0")
    assert result.returncode == 0, result.stderr
    assert "log out and back in" in result.stdout
    assert any(line.startswith("systemctl --user enable --now") for line in sandbox.calls())


def test_install_prints_a_summary_with_the_config_location(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="idle")
    config = sandbox.home / ".config" / "meeting-scribe" / ".env"
    assert str(config) in result.stdout
    assert "service" in result.stdout.lower()
    assert "extension" in result.stdout.lower()


def _installed(sandbox: Env) -> None:
    sandbox.run(INSTALL)
    sandbox.log.write_text("")


def test_uninstall_removes_files_and_disables_everything(sandbox: Env) -> None:
    _installed(sandbox)
    result = sandbox.run(UNINSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="idle")
    calls = sandbox.calls()
    assert result.returncode == 0, result.stderr
    assert not sandbox.unit_path.exists()
    assert not sandbox.activation_path.exists()
    assert f"systemctl --user disable --now {UNIT}" in calls
    assert f"gnome-extensions disable {EXTENSION}" in calls
    assert f"gnome-extensions uninstall {EXTENSION}" in calls
    assert "uv tool uninstall meeting-scribe" in calls
    assert _index(calls, "systemctl --user disable") < _index(
        calls, "systemctl --user daemon-reload"
    )


def test_uninstall_refuses_while_recording(sandbox: Env) -> None:
    _installed(sandbox)
    result = sandbox.run(UNINSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="recording")
    assert result.returncode != 0
    assert "stop the recording" in (result.stdout + result.stderr).lower()
    assert sandbox.unit_path.exists()
    assert not any("disable" in line or "uninstall" in line for line in sandbox.calls())


def test_uninstall_proceeds_while_recording_with_force(sandbox: Env) -> None:
    _installed(sandbox)
    result = sandbox.run(UNINSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="recording", FORCE="1")
    assert result.returncode == 0, result.stderr
    assert not sandbox.unit_path.exists()


def test_uninstall_is_idempotent_on_a_clean_home(sandbox: Env) -> None:
    result = sandbox.run(UNINSTALL)
    assert result.returncode == 0, result.stderr


def test_uninstall_keeps_the_user_config(sandbox: Env) -> None:
    config = sandbox.home / ".config" / "meeting-scribe" / ".env"
    config.parent.mkdir(parents=True)
    config.write_text("LOG_LEVEL=DEBUG\n")
    _installed(sandbox)
    sandbox.run(UNINSTALL)
    assert config.read_text() == "LOG_LEVEL=DEBUG\n"


@pytest.mark.parametrize("script", sorted(SCRIPTS.glob("*.sh")))
def test_scripts_never_reference_the_meetings_directory(script: Path) -> None:
    assert "Documents/meetings" not in script.read_text()
