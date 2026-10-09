"""Behavior of ``scripts/install.sh`` and ``scripts/uninstall.sh`` against fake system tools.

The scripts run in a throwaway HOME with a PATH that holds only fake ``uv``, ``systemctl``,
``gnome-extensions`` and ``gdbus`` plus symlinks to a whitelist of coreutils, so the real tools can
never run. Every fake appends its argv to a log file, which is also the proof the fakes were used.
"""

import re
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
  *is-active*) [ ! -e "$FAKE_LOG.failed" ] && [ "${FAKE_UNIT_ACTIVE:-0}" = 1 ] \\
               && exit 0 || exit 3 ;;
  *enable*|*restart*|*daemon-reload*)
    case "$*" in
      *"${FAKE_SYSTEMCTL_FAIL:-@none@}"*) touch "$FAKE_LOG.failed"; echo "Job failed" >&2; exit 1 ;;
    esac ;;
esac
""",
    "gnome-extensions": """
case "$1" in
  info) [ "${FAKE_EXT_KNOWN:-1}" = 1 ] && echo "State: ENABLED" && exit 0 || exit 1 ;;
  enable) [ "${FAKE_EXT_ENABLE_FAIL:-0}" = 1 ] && exit 2 || exit 0 ;;
esac
""",
    "gdbus": """
mode="${FAKE_GDBUS_MODE:-noowner}"
case "$*" in
  *NameHasOwner*)
    case "$mode" in
      owner-fail) echo "Error: timeout" >&2; exit 1 ;;
      owner-garbage) echo "what" ;;
      noowner) echo "(false,)" ;;
      *) echo "(true,)" ;;
    esac ;;
  *Properties.Get*)
    case "$mode" in
      recording) echo "(<'RECORDING'>,)" ;;
      weird) echo "(<'WEIRD'>,)" ;;
      stopped) echo "(<'STOPPED'>,)" ;;
      idle-then-recording)
        n="$(cat "$FAKE_LOG.gets" 2>/dev/null || echo 0)"; echo $((n + 1)) > "$FAKE_LOG.gets"
        [ "$n" = 0 ] && echo "(<'IDLE'>,)" || echo "(<'RECORDING'>,)" ;;
      get-fail) echo "Error: timeout" >&2; exit 1 ;;
      get-garbage) echo "what" ;;
      get-empty) ;;
      *) echo "(<'IDLE'>,)" ;;
    esac ;;
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


TOOLS = ["systemctl", "gnome-extensions", "uv", "gdbus"]


def test_scripts_only_reach_the_fakes_never_a_decoy_on_a_normal_path(sandbox: Env) -> None:
    decoys = sandbox.home / ".local" / "bin"
    decoys.mkdir(parents=True)
    marker = sandbox.log.parent / "REAL-CALLED"
    for tool in TOOLS:
        decoy = decoys / tool
        decoy.write_text(f'#!/usr/bin/env bash\necho "{tool}" >> "{marker}"\n')
        decoy.chmod(0o755)
    sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="idle")
    sandbox.run(UNINSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="idle")
    called = {line.split()[0] for line in sandbox.calls()}
    assert set(TOOLS) <= called  # the fakes ran
    assert not marker.exists()  # the decoys never did


def test_sandbox_path_holds_real_files_for_tools_and_only_whitelisted_links(
    sandbox: Env,
) -> None:
    fakes = Path(sandbox.env["PATH"])
    assert fakes.is_dir()
    assert ":" not in sandbox.env["PATH"]  # a single entry, nothing inherited
    assert fakes == sandbox.home.parent / "fakes"  # exactly the fakes directory
    for tool in TOOLS:
        assert not (fakes / tool).is_symlink()
    links = {p.name for p in fakes.iterdir() if p.is_symlink()}
    assert links <= set(COREUTILS)


@pytest.mark.parametrize("script", sorted(SCRIPTS.glob("*.sh")))
@pytest.mark.parametrize("tool", TOOLS)
def test_scripts_never_hardcode_an_absolute_path_to_a_tool(script: Path, tool: str) -> None:
    pattern = rf"/(?:usr|bin|sbin|opt|nix)[\w./-]*/{re.escape(tool)}\b"
    assert not re.search(pattern, script.read_text())


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


def test_install_refuses_while_recording_before_touching_anything(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="recording")
    assert result.returncode != 0
    message = (result.stdout + result.stderr).lower()
    assert "a recording is in progress" in message
    assert "top-bar menu" in message
    calls = sandbox.calls()
    assert not any(line.startswith(("uv tool install", "gnome-extensions")) for line in calls)
    assert not any(line.startswith("systemctl") and "is-active" not in line for line in calls)
    assert not sandbox.unit_path.exists()
    assert not sandbox.activation_path.exists()


GDBUS_FAILURES = [
    "owner-fail",
    "owner-garbage",
    "get-fail",
    "get-garbage",
    "get-empty",
    "weird",
]


@pytest.mark.parametrize("mode", GDBUS_FAILURES)
def test_install_fails_closed_when_the_state_cannot_be_read(sandbox: Env, mode: str) -> None:
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE=mode)
    assert result.returncode != 0
    assert "could not be read" in (result.stdout + result.stderr).lower()
    assert not any(line.startswith("uv tool install") for line in sandbox.calls())
    assert not any("restart" in line for line in sandbox.calls())
    assert not sandbox.unit_path.exists()


@pytest.mark.parametrize("mode", GDBUS_FAILURES)
def test_uninstall_fails_closed_when_the_state_cannot_be_read(sandbox: Env, mode: str) -> None:
    _installed(sandbox)
    result = sandbox.run(UNINSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE=mode)
    assert result.returncode != 0
    assert "could not be read" in (result.stdout + result.stderr).lower()
    assert sandbox.unit_path.exists()
    assert not any("disable" in line or "uninstall" in line for line in sandbox.calls())


def test_uninstall_proceeds_with_force_when_the_state_cannot_be_read(sandbox: Env) -> None:
    _installed(sandbox)
    result = sandbox.run(UNINSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="get-fail", FORCE="1")
    assert result.returncode == 0, result.stderr
    assert not sandbox.unit_path.exists()


@pytest.mark.parametrize("mode", ["get-fail", "weird"])
def test_install_proceeds_with_force_when_the_state_is_unknown(sandbox: Env, mode: str) -> None:
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="0", FAKE_GDBUS_MODE=mode, FORCE="1")
    assert result.returncode == 0, result.stderr
    assert any(line.startswith("uv tool install") for line in sandbox.calls())


def test_install_unknown_message_names_the_remedies(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_GDBUS_MODE="weird")
    message = result.stdout + result.stderr
    assert f"systemctl --user stop {UNIT}" in message
    assert "FORCE=1" in message


def test_install_never_proceeds_while_recording_even_with_force(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="recording", FORCE="1")
    assert result.returncode != 0
    assert not any(line.startswith("uv tool install") for line in sandbox.calls())


def test_install_treats_a_stopped_service_as_safe(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_GDBUS_MODE="stopped")
    assert result.returncode == 0, result.stderr


def test_install_skips_the_restart_when_a_recording_starts_during_the_install(
    sandbox: Env,
) -> None:
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="idle-then-recording")
    assert result.returncode == 0, result.stderr
    assert not any("restart" in line for line in sandbox.calls())
    assert "warning" in result.stderr.lower()
    assert "recording" in result.stderr.lower()
    assert "restart:   skipped" in result.stdout
    assert any(line.startswith("gnome-extensions install") for line in sandbox.calls())


def test_daemon_reload_failure_has_its_own_cause(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_GDBUS_MODE="idle", FAKE_SYSTEMCTL_FAIL="daemon-reload")
    assert result.returncode != 0
    assert "daemon-reload failed" in result.stdout
    assert "make run" not in result.stdout


def test_gdbus_calls_are_bounded_by_a_timeout(sandbox: Env) -> None:
    sandbox.run(INSTALL, FAKE_GDBUS_MODE="idle")
    gdbus_calls = [line for line in sandbox.calls() if line.startswith("gdbus ")]
    assert gdbus_calls
    assert all("--timeout" in line for line in gdbus_calls)


@pytest.mark.parametrize(
    ("active", "failing"), [("0", "enable"), ("1", "restart")], ids=["first-install", "reinstall"]
)
def test_start_failure_still_installs_the_extension_and_explains(
    sandbox: Env, active: str, failing: str
) -> None:
    result = sandbox.run(
        INSTALL, FAKE_UNIT_ACTIVE=active, FAKE_GDBUS_MODE="idle", FAKE_SYSTEMCTL_FAIL=failing
    )
    assert result.returncode != 0
    assert any(line.startswith("gnome-extensions install --force") for line in sandbox.calls())
    assert "NOT active" in result.stdout
    assert "make run" in result.stdout
    assert "Ctrl+C" in result.stdout
    assert f"systemctl --user restart {UNIT}" in result.stdout


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
    assert "a recording is in progress" in (result.stdout + result.stderr).lower()
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
