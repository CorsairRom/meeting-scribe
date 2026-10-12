"""Sandbox for the install, uninstall and preflight tests: a throwaway HOME and a PATH of fakes.

The scripts run in a throwaway HOME with a PATH that holds only fake system tools plus symlinks to a
whitelist of coreutils, so the real tools can never run. Every fake appends its argv to a log file,
which is also the proof the fakes were used.
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
ROOT_INSTALL = ROOT / "install.sh"
ROOT_UNINSTALL = ROOT / "uninstall.sh"
PREFLIGHT = SCRIPTS / "preflight.sh"
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
      echo "${FAKE_OLLAMA_MODEL:-qwen3:4b-instruct-2507-q4_K_M}  bbbbbbbbbbbb  2.5 GB  1 day ago"
    } ;;
  *) echo "ollama: unexpected call" >&2; exit 9 ;;
esac
""",
    # Records the call: the installer must never run it (it only prints sudo commands).
    "sudo": "echo 'sudo was called by the installer' >&2; exit 99",
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

    def snippet(self, code: str, **extra: str) -> subprocess.CompletedProcess[str]:
        """Run bash code with the common helpers and the preflight library already sourced."""
        header = f'source "{SCRIPTS}/_common.sh"; source "{PREFLIGHT}"; '
        return subprocess.run(  # noqa: S603  # fixed argv, sandboxed PATH
            [str(self.env["BASH"]), "-c", header + code],
            env={**self.env, **extra},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    def tree(self) -> list[str]:
        """Every path under HOME, relative and sorted: the proof that nothing was written."""
        return sorted(str(p.relative_to(self.home)) for p in self.home.rglob("*"))

    def run(self, script: Path, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        env = {**self.env, **extra}
        return subprocess.run(  # noqa: S603  # fixed argv, sandboxed PATH
            [str(self.env["BASH"]), str(script), *args],
            env=env,
            stdin=subprocess.DEVNULL,  # the scripts must never wait for input
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )


@pytest.fixture(name="sandbox")
def sandbox_fixture(tmp_path: Path) -> Env:
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
