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
DESKTOP_ENTRY = "dev.meetingscribe.Review.desktop"
COREUTILS = [
    "bash",
    "env",
    "mkdir",
    "chmod",
    "cp",
    "rm",
    "rmdir",
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
    "readlink",
    "timeout",
    "sleep",
]

FAKES = {
    "uv": """
case "$1 $2" in
  "tool install")
    echo "uv-env UV_TOOL_DIR=${UV_TOOL_DIR:-} UV_TOOL_BIN_DIR=${UV_TOOL_BIN_DIR:-}" >> "$FAKE_LOG"
    [ "${FAKE_UV_FAIL:-0}" = 1 ] && { mkdir -p "$UV_TOOL_DIR"; echo "uv: boom" >&2; exit 2; }
    mkdir -p "$UV_TOOL_BIN_DIR"
    [ "${FAKE_UV_NO_EXE:-0}" = 1 ] && exit 0
    # the installed entry point: only `prefetch-models` is ever run by the scripts
    cat > "$UV_TOOL_BIN_DIR/meeting-scribe" <<'EOS'
#!/usr/bin/env bash
echo "meeting-scribe $* (from $0)" >> "$FAKE_LOG"
if [ "$1" = prefetch-models ]; then
  echo "fake prefetch output"
  [ -n "${FAKE_PREFETCH_SLEEP:-}" ] && exec sleep "$FAKE_PREFETCH_SLEEP"
  [ "${FAKE_PREFETCH_FAIL:-0}" = 1 ] && { echo "FAILED [ACCESS_DENIED]" >&2; exit 1; }
  [ -n "${FAKE_PREFETCH_EXIT:-}" ] && exit "$FAKE_PREFETCH_EXIT"
fi
exit 0
EOS
    chmod +x "$UV_TOOL_BIN_DIR/meeting-scribe" ;;
  "tool dir")
    echo "uv-env-dir UV_TOOL_DIR=${UV_TOOL_DIR:-}" \\
         "UV_TOOL_BIN_DIR=${UV_TOOL_BIN_DIR:-}" >> "$FAKE_LOG"
    echo "${FAKE_UV_TOOL_BIN:-/legacy/.local/bin}" ;;
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
  pack|install)
    [ "${FAKE_EXT_FAIL:-}" = "$1" ] && { echo "ext $1 failed" >&2; exit 4; }
    exit 0 ;;
  info) [ "${FAKE_EXT_KNOWN:-1}" = 1 ] && echo "State: ENABLED" && exit 0 || exit 1 ;;
  enable) [ "${FAKE_EXT_ENABLE_FAIL:-0}" = 1 ] && exit 2 || exit 0 ;;
esac
""",
    # The review app's interpreter probe. Each probe fails when its own name is listed in
    # FAKE_GI_MISSING (comma separated: gi, Gtk, Adw). Any other call just succeeds.
    "python3": """
missing=",${FAKE_GI_MISSING:-},"
case "$*" in
  *Adw*) [[ "$missing" == *,Adw,* ]] && exit 1 ;;
  *Gtk*) [[ "$missing" == *,Gtk,* ]] && exit 1 ;;
  *"import gi"*) [[ "$missing" == *,gi,* ]] && exit 1 ;;
esac
echo "python3-run PYTHONPATH=${PYTHONPATH:-} PYTHONDONTWRITEBYTECODE=${PYTHONDONTWRITEBYTECODE:-}" \
     "args=$*" >> "$FAKE_LOG"
exit 0
""",
    # Read-only stand-in: only `list` is expected. FAKE_OLLAMA_MODE: ok (default), missing-model,
    # down (server unreachable), hang. Anything else is logged and fails, so a pull, run or serve
    # by the installer is visible in the log.
    "ollama": """
case "$1" in
  list)
    case "${FAKE_OLLAMA_MODE:-ok}" in
      down) echo "Error: could not connect to ollama server" >&2; exit 1 ;;
      hang) exec sleep 30 ;;
    esac
    echo "NAME                ID              SIZE      MODIFIED"
    echo "llama3.2:latest     aaaaaaaaaaaa    2.0 GB    2 days ago"
    [ "${FAKE_OLLAMA_MODE:-ok}" = missing-model ] || {
      echo "${FAKE_OLLAMA_MODEL:-qwen3.5:9b}     bbbbbbbbbbbb    6.6 GB    1 day ago"
    } ;;
  *) echo "ollama: unexpected call" >&2; exit 9 ;;
esac
""",
    "update-desktop-database": "",
    "gdbus": """
mode="${FAKE_GDBUS_MODE:-noowner}"
case "$*" in
  *NameHasOwner*)
    case "$mode" in
      idle-then-none)
        n="$(cat "$FAKE_LOG.owners" 2>/dev/null || echo 0)"; echo $((n + 1)) > "$FAKE_LOG.owners"
        [ "$n" = 0 ] && echo "(true,)" || echo "(false,)" ;;
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
    log: Path
    env: dict[str, str]

    @property
    def unit_path(self) -> Path:
        return self.home / ".config" / "systemd" / "user" / UNIT

    @property
    def activation_path(self) -> Path:
        return self.home / ".local" / "share" / "dbus-1" / "services" / ACTIVATION

    @property
    def root(self) -> Path:
        return self.home / ".local" / "share" / "meeting-scribe"

    @property
    def desktop_entry(self) -> Path:
        return self.home / ".local" / "share" / "applications" / DESKTOP_ENTRY

    @property
    def exe(self) -> Path:
        return self.root / "current" / "bin" / "meeting-scribe"

    @property
    def versions(self) -> list[Path]:
        folder = self.root / "versions"
        return sorted(folder.iterdir()) if folder.exists() else []

    @property
    def unit_exec_start(self) -> str:
        lines = self.unit_path.read_text().splitlines()
        return next(line for line in lines if line.startswith("ExecStart="))

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def run(self, script: Path, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        env = {**self.env, **extra}
        return subprocess.run(  # noqa: S603  # fixed argv, sandboxed PATH
            [str(self.env["BASH"]), str(script), *args],
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
    env = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "PATH": str(fakes),
        "FAKE_LOG": str(log),
        "BASH": str(shutil.which("bash")),
        "REVIEW_APP_PYTHON": str(fakes / "python3"),
    }
    return Env(home=home, log=log, env=env)


def _is_restart(line: str) -> bool:
    return line.startswith("systemctl") and "restart" in line


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
    expected = f"uv tool install --force --reinstall --python 3.12 meeting-scribe[ml] @ {ROOT}"
    assert expected in sandbox.calls()  # the ml extra: faster-whisper, torch, pyannote


def _prefetches(sandbox: Env) -> list[str]:
    return [line for line in sandbox.calls() if line.startswith("meeting-scribe prefetch-models")]


def test_install_downloads_the_models_with_the_new_version_after_it_is_active(
    sandbox: Env,
) -> None:
    result = sandbox.run(INSTALL, **IDLE)
    assert result.returncode == 0, result.stderr
    (version,) = sandbox.versions
    (call,) = _prefetches(sandbox)
    assert call == f"meeting-scribe prefetch-models (from {version}/bin/meeting-scribe)"
    calls = sandbox.calls()
    assert _index(calls, f"systemctl --user enable --now {UNIT}") < _index(
        calls, "meeting-scribe prefetch-models"
    )
    assert _index(calls, "meeting-scribe prefetch-models") < _index(calls, "gnome-extensions pack")
    assert "==> Downloading the speech models" in result.stdout
    assert "fake prefetch output" in result.stdout  # visible, not swallowed
    assert "models:    downloaded" in result.stdout


def test_a_failed_model_download_does_not_fail_the_service_install_but_is_loud(
    sandbox: Env,
) -> None:
    result = sandbox.run(INSTALL, FAKE_PREFETCH_FAIL="1", **IDLE)
    assert result.returncode == 0, result.stderr
    assert sandbox.exe.exists()  # the service and the unit are installed and switched
    assert sandbox.unit_path.exists()
    summary = result.stdout.split("meeting-scribe installed")[1]
    assert "models:    NOT downloaded" in summary
    assert "hf auth login" in summary
    assert "current/bin/meeting-scribe prefetch-models" in summary  # retry once fixed


def test_no_model_download_after_a_rollback_and_the_summary_says_why(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_SYSTEMCTL_FAIL="restart", **IDLE)
    assert len(_prefetches(sandbox)) == 1  # only the first install fetched
    summary = result.stdout.split("meeting-scribe installed")[1]
    assert "models:    NOT downloaded, the install rolled back" in summary
    assert "start failure is the real problem" in summary
    assert "==> Downloading the speech models" not in result.stdout
    assert "NOT downloaded; recording works" not in summary


def test_a_hung_model_download_times_out_and_the_install_still_finishes(sandbox: Env) -> None:
    result = sandbox.run(
        INSTALL,
        FAKE_PREFETCH_SLEEP="30",
        MEETING_SCRIBE_PREFETCH_TIMEOUT="1",
        **IDLE,
    )
    assert result.returncode == 0, result.stderr
    assert "gnome-extensions pack" in " ".join(sandbox.calls())  # the extension step still ran
    summary = result.stdout.split("meeting-scribe installed")[1]
    assert "models:    NOT downloaded, the download timed out after 1 s" in summary
    assert "current/bin/meeting-scribe prefetch-models" in summary
    assert "MEETING_SCRIBE_PREFETCH_TIMEOUT" in summary


def test_a_sigkilled_model_download_is_reported_as_timed_out(sandbox: Env) -> None:
    # timeout --kill-after exits 137 when it had to SIGKILL a download that ignored SIGTERM
    result = sandbox.run(INSTALL, FAKE_PREFETCH_EXIT="137", **IDLE)
    assert result.returncode == 0, result.stderr
    summary = result.stdout.split("meeting-scribe installed")[1]
    assert "the download timed out after 3600 s" in summary
    assert "NOT downloaded; recording works" not in summary


@pytest.mark.parametrize("value", ["abc", "0", "-5", "1.5", ""])
def test_an_invalid_prefetch_timeout_warns_once_and_falls_back_to_the_default(
    sandbox: Env, value: str
) -> None:
    result = sandbox.run(
        INSTALL, MEETING_SCRIBE_PREFETCH_TIMEOUT=value, FAKE_PREFETCH_EXIT="124", **IDLE
    )
    assert result.returncode == 0, result.stderr
    if value == "":  # empty means unset: no warning, the default applies
        assert "MEETING_SCRIBE_PREFETCH_TIMEOUT" not in result.stderr
    else:
        assert result.stderr.count("MEETING_SCRIBE_PREFETCH_TIMEOUT") == 1
        assert "positive integer" in result.stderr
    assert len(_prefetches(sandbox)) == 1  # the download still ran
    assert "timed out after 3600 s" in result.stdout.split("meeting-scribe installed")[1]


def test_the_summary_does_not_hardcode_a_pipeline_url(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_PREFETCH_FAIL="1", **IDLE)
    summary = result.stdout.split("meeting-scribe installed")[1]
    assert "hf.co/pyannote" not in summary
    assert "output above" in summary
    assert "current/bin/meeting-scribe prefetch-models" in summary


def test_no_model_download_when_the_install_is_refused_or_uv_fails(sandbox: Env) -> None:
    sandbox.run(INSTALL, FAKE_GDBUS_MODE="recording")
    sandbox.run(INSTALL, FAKE_UV_FAIL="1", **IDLE)
    assert _prefetches(sandbox) == []


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
    assert not any(_is_restart(line) for line in sandbox.calls())
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


@pytest.mark.parametrize("mode", ["get-fail", "weird"])
def test_forced_install_restarts_an_active_service_whose_state_stays_unknown(
    sandbox: Env, mode: str
) -> None:
    # FORCE=1 is the documented remedy for a wedged service: it must actually replace it.
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE=mode, FORCE="1")
    assert result.returncode == 0, result.stderr
    assert f"systemctl --user restart {UNIT}" in sandbox.calls()


def test_forced_install_still_never_restarts_a_recording(sandbox: Env) -> None:
    result = sandbox.run(
        INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="idle-then-recording", FORCE="1"
    )
    assert result.returncode == 0, result.stderr
    assert not any(_is_restart(line) for line in sandbox.calls())
    assert "restart:   skipped" in result.stdout


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
    assert not any(_is_restart(line) for line in sandbox.calls())
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


# --- F2.7: versioned installs, truthful summary, one FORCE contract, escaped rendering ---

IDLE = {"FAKE_GDBUS_MODE": "idle"}


def test_install_gives_each_version_its_own_uv_tool_and_bin_dirs(sandbox: Env) -> None:
    result = sandbox.run(INSTALL)
    assert result.returncode == 0, result.stderr
    (version,) = sandbox.versions
    env_line = next(line for line in sandbox.calls() if line.startswith("uv-env"))
    assert f"UV_TOOL_DIR={version / 'tools'}" in env_line
    assert f"UV_TOOL_BIN_DIR={version / 'bin'}" in env_line
    assert (sandbox.root / "current").is_symlink()
    assert (sandbox.root / "current").resolve() == version.resolve()
    assert sandbox.exe.is_file()


def test_exec_start_path_is_stable_across_versions(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    first = sandbox.unit_exec_start
    sandbox.run(INSTALL, **IDLE)
    assert sandbox.unit_exec_start == first == f"ExecStart={sandbox.exe}"


def test_second_install_keeps_the_running_version_while_the_restart_is_skipped(
    sandbox: Env,
) -> None:
    sandbox.run(INSTALL, **IDLE)
    (first,) = sandbox.versions
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="idle-then-recording")
    assert result.returncode == 0, result.stderr
    assert "restart:   skipped" in result.stdout
    assert first in sandbox.versions  # the recording may still run from it
    assert len(sandbox.versions) == 2
    current = (sandbox.root / "current").resolve()
    assert current != first.resolve()
    assert current in [v.resolve() for v in sandbox.versions]


def test_old_versions_are_pruned_after_a_successful_restart(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="idle-then-recording")
    assert len(sandbox.versions) == 2
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", **IDLE)
    assert result.returncode == 0, result.stderr
    assert f"systemctl --user restart {UNIT}" in sandbox.calls()
    (only,) = sandbox.versions
    assert (sandbox.root / "current").resolve() == only.resolve()


def test_nothing_is_pruned_when_the_restart_failed(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_SYSTEMCTL_FAIL="restart", **IDLE)
    assert result.returncode != 0
    assert len(sandbox.versions) == 2


def test_a_failed_uv_install_leaves_current_and_the_unit_untouched(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    (first,) = sandbox.versions
    unit_before = sandbox.unit_path.read_text()
    result = sandbox.run(INSTALL, FAKE_UV_FAIL="1", **IDLE)
    assert result.returncode != 0
    assert sandbox.versions == [first]
    assert (sandbox.root / "current").resolve() == first.resolve()
    assert sandbox.unit_path.read_text() == unit_before


def _legacy_unit(sandbox: Env) -> None:
    sandbox.unit_path.parent.mkdir(parents=True)
    sandbox.unit_path.write_text("[Service]\nExecStart=/legacy/.local/bin/meeting-scribe\n")


def test_migration_removes_the_old_uv_tool_only_after_the_new_unit_is_active(
    sandbox: Env,
) -> None:
    _legacy_unit(sandbox)
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", **IDLE)
    calls = sandbox.calls()
    assert result.returncode == 0, result.stderr
    assert sandbox.unit_exec_start == f"ExecStart={sandbox.exe}"
    assert _index(calls, f"systemctl --user restart {UNIT}") < _index(
        calls, "uv tool uninstall meeting-scribe"
    )


def test_migration_keeps_the_old_uv_tool_while_the_old_service_may_still_run(
    sandbox: Env,
) -> None:
    _legacy_unit(sandbox)
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="idle-then-recording")
    assert result.returncode == 0, result.stderr
    assert not any(line.startswith("uv tool uninstall") for line in sandbox.calls())
    assert "uv tool uninstall meeting-scribe" in result.stdout  # tells the user how


def test_a_regular_install_never_uninstalls_a_uv_tool(sandbox: Env) -> None:
    sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", **IDLE)
    sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", **IDLE)
    assert not any(line.startswith("uv tool uninstall") for line in sandbox.calls())


def test_uninstall_removes_the_versions_tree(sandbox: Env) -> None:
    _installed(sandbox)
    assert sandbox.root.exists()
    sandbox.run(UNINSTALL, **IDLE)
    assert not sandbox.root.exists()


def test_uninstall_keeps_other_data_next_to_the_versions(sandbox: Env) -> None:
    # Only what the installer created goes away; e.g. a future model cache must survive.
    _installed(sandbox)
    other = sandbox.root / "models" / "keep.bin"
    other.parent.mkdir(parents=True)
    other.write_text("x")
    sandbox.run(UNINSTALL, **IDLE)
    assert other.exists()
    assert not (sandbox.root / "versions").exists()
    assert not (sandbox.root / "current").is_symlink()


@pytest.mark.parametrize("failing", ["pack", "install"])
def test_extension_step_failure_still_prints_the_summary_and_exits_non_zero(
    sandbox: Env, failing: str
) -> None:
    result = sandbox.run(INSTALL, FAKE_EXT_FAIL=failing, **IDLE)
    assert result.returncode != 0
    assert "meeting-scribe installed" in result.stdout
    assert "service:" in result.stdout
    assert f"extension: FAILED ({failing}" in result.stdout
    assert f"ext {failing} failed" in result.stderr


def test_extension_failure_keeps_the_service_failure_cause(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_EXT_FAIL="pack", FAKE_SYSTEMCTL_FAIL="enable", **IDLE)
    assert result.returncode != 0
    assert "NOT active" in result.stdout
    assert "extension: FAILED" in result.stdout


def test_log_out_hint_does_not_claim_a_running_service_when_it_is_not_active(
    sandbox: Env,
) -> None:
    result = sandbox.run(
        INSTALL, FAKE_EXT_KNOWN="0", FAKE_EXT_ENABLE_FAIL="1", FAKE_SYSTEMCTL_FAIL="enable", **IDLE
    )
    assert "log out and back in" in result.stdout
    assert "already running" not in result.stdout
    assert "NOT active" in result.stdout


def test_log_out_hint_mentions_the_service_when_it_is_active(sandbox: Env) -> None:
    result = sandbox.run(
        INSTALL, FAKE_EXT_KNOWN="0", FAKE_EXT_ENABLE_FAIL="1", FAKE_UNIT_ACTIVE="1", **IDLE
    )
    assert "log out and back in" in result.stdout
    assert "service is already running" in result.stdout


def test_start_failure_cause_lists_the_bus_conflict_as_one_possible_cause(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_SYSTEMCTL_FAIL="enable", **IDLE)
    assert "possible cause" in result.stdout.lower()
    assert "make run" in result.stdout
    assert f"journalctl --user -u {UNIT}" in result.stdout


def test_uninstall_uses_the_shared_state_check_even_with_force(sandbox: Env) -> None:
    _installed(sandbox)
    result = sandbox.run(UNINSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="recording", FORCE="1")
    assert result.returncode == 0, result.stderr
    assert any(line.startswith("gdbus ") for line in sandbox.calls())
    assert "FORCE=1" in result.stderr
    assert "recording" in result.stderr.lower()


def test_install_force_never_overrides_a_recording_detected_at_the_first_check(
    sandbox: Env,
) -> None:
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="recording", FORCE="1")
    assert result.returncode != 0
    assert "a recording is in progress" in result.stderr.lower()
    assert not any(line.startswith("uv tool install") for line in sandbox.calls())
    assert not sandbox.unit_path.exists()


def test_uninstall_force_proceeds_over_a_recording_by_intent(sandbox: Env) -> None:
    _installed(sandbox)
    result = sandbox.run(UNINSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="recording", FORCE="1")
    assert result.returncode == 0, result.stderr
    assert f"systemctl --user disable --now {UNIT}" in sandbox.calls()
    assert not sandbox.unit_path.exists()


def test_unit_is_rendered_verbatim_for_a_path_with_sed_special_characters(
    sandbox: Env, tmp_path: Path
) -> None:
    tricky = tmp_path / "a&b|c"
    result = sandbox.run(INSTALL, XDG_DATA_HOME=str(tricky), **IDLE)
    assert result.returncode == 0, result.stderr
    expected = f"ExecStart={tricky}/meeting-scribe/current/bin/meeting-scribe"
    lines = sandbox.unit_path.read_text().splitlines()
    assert expected in lines
    assert "@EXEC_START@" not in sandbox.unit_path.read_text()


def test_reinstall_with_a_failing_enable_does_not_restart_and_explains(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_SYSTEMCTL_FAIL="enable", **IDLE)
    assert result.returncode != 0
    assert not any(_is_restart(line) for line in sandbox.calls())
    assert "NOT active" in result.stdout
    assert f"journalctl --user -u {UNIT}" in result.stdout


def test_reinstall_restarts_when_the_service_exited_during_the_install(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_GDBUS_MODE="idle-then-none")
    assert result.returncode == 0, result.stderr
    assert f"systemctl --user restart {UNIT}" in sandbox.calls()
    assert "restart:   skipped" not in result.stdout


# --- F2.7b: verify before switching, roll back on a failed start, explicit legacy detection ---


def _restarts(sandbox: Env) -> list[str]:
    return [line for line in sandbox.calls() if _is_restart(line)]


def test_a_failed_restart_on_upgrade_rolls_back_to_the_previous_version(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    (first,) = sandbox.versions
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_SYSTEMCTL_FAIL="restart", **IDLE)
    assert result.returncode != 0
    assert (sandbox.root / "current").resolve() == first.resolve()
    assert len(_restarts(sandbox)) == 2  # the new version, then the previous one
    assert len(sandbox.versions) == 2  # the new one is kept for inspection
    assert f"rolled back to {first.name}" in result.stdout
    assert "kept in versions/" in result.stdout
    assert f"journalctl --user -u {UNIT}" in result.stdout
    assert sandbox.unit_exec_start == f"ExecStart={sandbox.exe}"


def test_a_failed_restart_on_migration_restores_the_legacy_unit_and_keeps_the_uv_tool(
    sandbox: Env,
) -> None:
    _legacy_unit(sandbox)
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_SYSTEMCTL_FAIL="restart", **IDLE)
    assert result.returncode != 0
    assert sandbox.unit_exec_start == "ExecStart=/legacy/.local/bin/meeting-scribe"
    assert not any(line.startswith("uv tool uninstall") for line in sandbox.calls())
    assert not (sandbox.root / "current").is_symlink()
    assert len(sandbox.versions) == 1  # kept for inspection
    assert "legacy" in result.stdout
    assert len(_restarts(sandbox)) == 2


def test_a_missing_executable_never_switches_and_removes_the_orphan(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    (first,) = sandbox.versions
    unit_before = sandbox.unit_path.read_text()
    result = sandbox.run(INSTALL, FAKE_UV_NO_EXE="1", **IDLE)
    assert result.returncode != 0
    assert "was not created" in result.stderr
    assert sandbox.versions == [first]
    assert (sandbox.root / "current").resolve() == first.resolve()
    assert sandbox.unit_path.read_text() == unit_before


def test_a_leftover_temp_link_from_an_interrupted_run_does_not_break_the_install(
    sandbox: Env,
) -> None:
    sandbox.run(INSTALL, **IDLE)
    # `exec` keeps the pid, so the leftover has exactly the name the old script would reuse.
    prelude = f'mkdir -p "{sandbox.root}"; ln -s x "{sandbox.root}/current.new.$$"; exec "$0" "$1"'
    result = subprocess.run(  # noqa: S603  # fixed argv, sandboxed PATH
        [sandbox.env["BASH"], "-c", prelude, sandbox.env["BASH"], str(INSTALL)],
        env={**sandbox.env, **IDLE},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert (sandbox.root / "current").resolve() in [v.resolve() for v in sandbox.versions]


def test_a_hand_edited_unit_is_not_mistaken_for_the_legacy_install(sandbox: Env) -> None:
    sandbox.unit_path.parent.mkdir(parents=True)
    sandbox.unit_path.write_text("[Service]\nExecStart=/opt/custom/meeting-scribe\n")
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", **IDLE)
    assert result.returncode == 0, result.stderr
    assert not any(line.startswith("uv tool uninstall") for line in sandbox.calls())


def test_the_legacy_install_is_the_unit_pointing_at_the_default_uv_tool_bin_dir(
    sandbox: Env,
) -> None:
    sandbox.unit_path.parent.mkdir(parents=True)
    sandbox.unit_path.write_text("[Service]\nExecStart=/elsewhere/bin/meeting-scribe\n")
    sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_UV_TOOL_BIN="/elsewhere/bin", **IDLE)
    calls = sandbox.calls()
    assert "uv tool uninstall meeting-scribe" in calls
    assert "uv tool dir --bin" in calls
    query_env = next(line for line in calls if line.startswith("uv-env-dir"))
    assert query_env == "uv-env-dir UV_TOOL_DIR= UV_TOOL_BIN_DIR="


# -- the review app (F4.6) -------------------------------------------------------------------


def _version_dir(sandbox: Env) -> Path:
    (version,) = sandbox.versions
    return version


def test_install_copies_the_review_app_into_the_version_without_bytecode(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, **IDLE)
    assert result.returncode == 0, result.stderr
    package = _version_dir(sandbox) / "app" / "meeting_scribe_review"
    assert (package / "__main__.py").is_file()
    assert (package / "ui" / "window.py").is_file()
    assert not list((_version_dir(sandbox) / "app").rglob("__pycache__"))


def test_the_launcher_runs_the_app_with_system_python_from_its_own_version(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    launcher = _version_dir(sandbox) / "bin" / "meeting-scribe-review"
    assert launcher.stat().st_mode & 0o111
    sandbox.log.write_text("")
    done = sandbox.run(launcher, "--meeting", "m1")
    assert done.returncode == 0, done.stderr
    (call,) = [line for line in sandbox.calls() if line.startswith("python3-run")]
    assert f"PYTHONPATH={_version_dir(sandbox)}/app" in call
    assert "PYTHONDONTWRITEBYTECODE=1" in call
    assert "args=-m meeting_scribe_review --meeting m1" in call


def test_the_launcher_interpreter_is_the_system_one_by_default() -> None:
    text = (SCRIPTS / "_common.sh").read_text()
    assert 'REVIEW_APP_PYTHON="${REVIEW_APP_PYTHON:-/usr/bin/python3}"' in text


def _exec_argv(entry: Path) -> list[str]:
    """Parse the Exec line the way the Desktop Entry specification says a launcher must."""
    line = next(x for x in entry.read_text().splitlines() if x.startswith("Exec="))
    value = line.removeprefix("Exec=").replace("\\\\", "\x00")  # string level: \\ -> \
    value = value.replace("\x00", "\\")
    argv: list[str] = []
    current, quoted, started, i = "", False, False, 0
    while i < len(value):
        ch = value[i]
        if quoted and ch == "\\":
            current += value[i + 1]
            i += 2
            continue
        if ch == '"':
            quoted, started = not quoted, True
        elif ch == " " and not quoted:
            if started:
                argv.append(current)
            current, started = "", False
        else:
            current, started = current + ch, True
        i += 1
    if started:
        argv.append(current)
    return argv


def test_install_renders_the_desktop_entry_with_the_stable_current_path(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    lines = sandbox.desktop_entry.read_text().splitlines()
    launcher = sandbox.root / "current" / "bin" / "meeting-scribe-review"
    assert _exec_argv(sandbox.desktop_entry) == [str(launcher)]
    for expected in (
        "Type=Application",
        "Icon=audio-input-microphone",
        "Categories=GNOME;GTK;Utility;",
        "StartupNotify=true",
        "StartupWMClass=dev.meetingscribe.Review",
        "DBusActivatable=false",
    ):
        assert expected in lines
    assert not any("@" in line for line in lines)  # every placeholder was rendered


def test_the_exec_line_is_spec_valid_for_spaces_and_reserved_characters(
    sandbox: Env, tmp_path: Path
) -> None:
    data = tmp_path / 'a b&c|d$e`f"g\\h%i' / "share"
    result = sandbox.run(INSTALL, XDG_DATA_HOME=str(data), **IDLE)
    assert result.returncode == 0, result.stderr
    entry = data / "applications" / DESKTOP_ENTRY
    expected = f"{data}/meeting-scribe/current/bin/meeting-scribe-review"
    assert _exec_argv(entry) == [expected.replace("%", "%%")]
    line = next(x for x in entry.read_text().splitlines() if x.startswith("Exec="))
    assert line.startswith('Exec="') and line.endswith('"')  # one quoted argument


def test_a_rollback_to_a_version_without_the_launcher_removes_the_entry(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    (first,) = sandbox.versions
    (first / "bin" / "meeting-scribe-review").unlink()  # stands in for a pre-F4 install
    result = sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_SYSTEMCTL_FAIL="restart", **IDLE)
    assert "rolled back" in result.stdout
    assert not sandbox.desktop_entry.exists()
    assert "review app: " in result.stdout  # the summary still reports the new, kept version


def test_a_rollback_to_a_version_with_the_launcher_keeps_the_entry(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_SYSTEMCTL_FAIL="restart", **IDLE)
    assert sandbox.desktop_entry.exists()


def test_the_entry_is_written_only_after_the_switch_succeeded(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_UV_FAIL="1", **IDLE)
    assert result.returncode != 0
    assert not sandbox.desktop_entry.exists()


def test_a_failed_first_start_leaves_an_entry_for_the_kept_version(sandbox: Env) -> None:
    # No previous version to roll back to: the new one stays in charge, so its entry stays.
    sandbox.run(INSTALL, FAKE_SYSTEMCTL_FAIL="enable", **IDLE)
    assert sandbox.desktop_entry.exists()


def test_install_refreshes_the_desktop_database_when_the_tool_exists(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    applications = sandbox.desktop_entry.parent
    assert f"update-desktop-database {applications}" in sandbox.calls()


def test_install_works_without_update_desktop_database(sandbox: Env) -> None:
    (Path(sandbox.env["PATH"]) / "update-desktop-database").unlink()
    result = sandbox.run(INSTALL, **IDLE)
    assert result.returncode == 0, result.stderr
    assert sandbox.desktop_entry.exists()


def test_the_summary_says_the_review_app_is_ready(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, **IDLE)
    summary = result.stdout.split("meeting-scribe installed")[1]
    assert "review app: ready" in summary


@pytest.mark.parametrize(
    ("missing", "packages"),
    [
        ("gi", ["python3-gobject", "gtk4", "libadwaita"]),
        ("Gtk", ["gtk4"]),
        ("Adw", ["libadwaita"]),
    ],
)
def test_a_missing_system_package_only_warns_and_names_the_packages(
    sandbox: Env, missing: str, packages: list[str]
) -> None:
    result = sandbox.run(INSTALL, FAKE_GI_MISSING=missing, **IDLE)
    assert result.returncode == 0, result.stderr
    summary = result.stdout.split("meeting-scribe installed")[1]
    line = next(x for x in summary.splitlines() if "review app:" in x)
    assert "NOT usable" in line
    for package in packages:
        assert package in line
    assert "gstreamer1-plugins-good" in summary
    assert sandbox.desktop_entry.exists()  # still installed, only the toolkit is missing


def test_an_absent_system_python_is_reported_the_same_way(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, REVIEW_APP_PYTHON="/nonexistent/python3", **IDLE)
    assert result.returncode == 0, result.stderr
    summary = result.stdout.split("meeting-scribe installed")[1]
    assert "review app: NOT usable (missing: python3-gobject" in summary


def test_the_review_app_never_runs_a_window_during_install(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    runs = [line for line in sandbox.calls() if line.startswith("python3-run")]
    assert all("args=-c " in line for line in runs)  # only the import probes, never the app
    assert not any("meeting_scribe_review" in line for line in runs)


def test_uninstall_removes_the_desktop_entry_and_the_app_but_not_other_entries(
    sandbox: Env,
) -> None:
    _installed(sandbox)
    other = sandbox.desktop_entry.parent / "other.desktop"
    other.write_text("[Desktop Entry]\n")
    result = sandbox.run(UNINSTALL, **IDLE)
    assert result.returncode == 0, result.stderr
    assert not sandbox.desktop_entry.exists()
    assert other.exists()
    assert not sandbox.root.exists()  # the launcher and the app went with versions/
    assert f"update-desktop-database {other.parent}" in sandbox.calls()


def test_uninstall_without_an_entry_is_still_clean(sandbox: Env) -> None:
    result = sandbox.run(UNINSTALL, **IDLE)
    assert result.returncode == 0, result.stderr


def test_a_rolled_back_install_keeps_the_previous_launcher_reachable(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    (first,) = sandbox.versions
    sandbox.run(INSTALL, FAKE_UNIT_ACTIVE="1", FAKE_SYSTEMCTL_FAIL="restart", **IDLE)
    assert (sandbox.root / "current").resolve() == first.resolve()
    assert (sandbox.root / "current" / "bin" / "meeting-scribe-review").is_file()


def test_make_run_review_app_uses_system_python_and_the_working_tree() -> None:
    dry = subprocess.run(
        ["make", "-n", "run-review-app"],  # noqa: S607  # dry run: prints, never runs
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert dry.returncode == 0, dry.stderr
    assert "PYTHONPATH=review-app/src" in dry.stdout
    assert "/usr/bin/python3 -m meeting_scribe_review" in dry.stdout
    override = subprocess.run(
        ["make", "-n", "run-review-app", "REVIEW_APP_PYTHON=/opt/py/python3"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "/opt/py/python3 -m meeting_scribe_review" in override.stdout


def test_the_desktop_template_has_exactly_one_exec_placeholder() -> None:
    template = (ROOT / "packaging" / "applications" / f"{DESKTOP_ENTRY}.in").read_text()
    assert template.count("@EXEC@") == 1  # the bash render splits on the first occurrence


# -- Ollama and the local summary model (F5.8, AC-20): read-only, warns, never fails the install --


def _summary(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout.split("meeting-scribe installed")[1]


def _ollama_calls(sandbox: Env) -> list[str]:
    return [line for line in sandbox.calls() if line.startswith("ollama")]


def test_install_reports_a_reachable_ollama_with_the_default_model(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, **IDLE)
    assert result.returncode == 0, result.stderr
    assert "  ollama:    reachable; model qwen3.5:9b present" in _summary(result)
    assert _ollama_calls(sandbox) == ["ollama list"]  # read-only: no pull, run, serve or create


def test_a_missing_model_only_warns_with_the_pull_command_and_never_pulls(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_OLLAMA_MODE="missing-model", **IDLE)
    assert result.returncode == 0, result.stderr
    summary = _summary(result)
    assert "  ollama:    reachable; model qwen3.5:9b NOT found" in summary
    assert "ollama pull qwen3.5:9b" in summary
    assert _ollama_calls(sandbox) == ["ollama list"]


def test_a_stopped_ollama_only_warns_and_the_install_still_succeeds(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, FAKE_OLLAMA_MODE="down", **IDLE)
    assert result.returncode == 0, result.stderr
    summary = _summary(result)
    assert "  ollama:    found but not reachable" in summary
    assert "systemctl start ollama" in summary
    assert sandbox.exe.exists()
    assert _ollama_calls(sandbox) == ["ollama list"]


def test_an_ollama_that_is_not_installed_only_warns(sandbox: Env) -> None:
    (Path(sandbox.env["PATH"]) / "ollama").unlink()
    result = sandbox.run(INSTALL, **IDLE)
    assert result.returncode == 0, result.stderr
    summary = _summary(result)
    assert "  ollama:    NOT found" in summary
    assert "ollama pull qwen3.5:9b" in summary
    assert _ollama_calls(sandbox) == []


def test_a_hung_ollama_is_cut_off_and_the_install_still_finishes(sandbox: Env) -> None:
    result = sandbox.run(
        INSTALL, FAKE_OLLAMA_MODE="hang", MEETING_SCRIBE_OLLAMA_TIMEOUT="1", **IDLE
    )
    assert result.returncode == 0, result.stderr
    assert "  ollama:    found but not reachable" in _summary(result)


def test_the_configured_model_comes_from_the_environment(sandbox: Env) -> None:
    result = sandbox.run(
        INSTALL, SUMMARIES_LOCAL_MODEL="mistral:7b", FAKE_OLLAMA_MODEL="mistral:7b", **IDLE
    )
    assert "model mistral:7b present" in _summary(result)


def test_the_configured_model_comes_from_the_settings_file_and_nothing_else_is_read(
    sandbox: Env,
) -> None:
    config = sandbox.home / ".config" / "meeting-scribe"
    config.mkdir(parents=True)
    (config / ".env").write_text('HF_TOKEN=not-a-real-token\nSUMMARIES_LOCAL_MODEL="gemma3:4b"  \n')
    result = sandbox.run(INSTALL, FAKE_OLLAMA_MODEL="gemma3:4b", **IDLE)
    assert "model gemma3:4b present" in _summary(result)
    assert "not-a-real-token" not in result.stdout + result.stderr


def test_a_model_name_without_a_tag_matches_its_latest_tag(sandbox: Env) -> None:
    result = sandbox.run(
        INSTALL, SUMMARIES_LOCAL_MODEL="llama3.2", **IDLE
    )  # the fake lists llama3.2:latest
    assert "model llama3.2 present" in _summary(result)


def test_a_model_name_is_matched_whole_not_as_a_prefix(sandbox: Env) -> None:
    result = sandbox.run(INSTALL, SUMMARIES_LOCAL_MODEL="llama3", **IDLE)
    assert "model llama3 NOT found" in _summary(result)


def test_ollama_is_not_checked_when_the_install_is_refused_or_uv_fails(sandbox: Env) -> None:
    sandbox.run(INSTALL, FAKE_GDBUS_MODE="recording")
    sandbox.run(INSTALL, FAKE_UV_FAIL="1", **IDLE)
    assert _ollama_calls(sandbox) == []


def test_uninstall_never_touches_ollama(sandbox: Env) -> None:
    sandbox.run(INSTALL, **IDLE)
    sandbox.log.write_text("")
    sandbox.run(UNINSTALL, **IDLE)
    assert _ollama_calls(sandbox) == []


@pytest.mark.parametrize(
    "line",
    [
        'SUMMARIES_LOCAL_MODEL="gemma3:4b"  # the small one\r\n',  # CRLF, quotes, inline comment
        "SUMMARIES_LOCAL_MODEL='gemma3:4b'\r\n",
        "SUMMARIES_LOCAL_MODEL=  gemma3:4b   # note\n",
        "SUMMARIES_LOCAL_MODEL=gemma3:4b\t#note\n",
    ],
)
def test_the_model_line_is_parsed_robustly(sandbox: Env, line: str) -> None:
    config = sandbox.home / ".config" / "meeting-scribe"
    config.mkdir(parents=True)
    (config / ".env").write_bytes(line.encode())
    result = sandbox.run(INSTALL, FAKE_OLLAMA_MODEL="gemma3:4b", **IDLE)
    assert "model gemma3:4b present" in _summary(result)


def test_a_model_name_containing_a_hash_is_kept_when_no_space_precedes_it(sandbox: Env) -> None:
    config = sandbox.home / ".config" / "meeting-scribe"
    config.mkdir(parents=True)
    (config / ".env").write_text("SUMMARIES_LOCAL_MODEL=org/model#1\n")
    result = sandbox.run(INSTALL, **IDLE)
    assert "model org/model#1 NOT found" in _summary(result)
